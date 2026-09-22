from __future__ import annotations

import os
import sys
import time
from typing import Any, cast
from uuid import uuid4

PROMPT = "Reply with one short greeting for this synthetic test."


class SmokeFailure(Exception):
    pass


def _require_staging() -> None:
    expected = {
        "PYTHON_ENV": "staging",
        "AUTHORITY_KERNEL_ENABLED": "true",
        "AGENT_API_ENABLED": "true",
    }
    if any(
        os.environ.get(name, "").lower() != value for name, value in expected.items()
    ):
        raise SmokeFailure


def _eligible_candidate() -> tuple[str, str]:
    from app.configuration.authority import AUTHORITY_KERNEL_SETTINGS

    from activemodel.session_manager import get_session
    from app.models.authority import (
        AuthorityActor,
        AuthorityActorExternalIdentity,
        AuthorityPersonBinding,
        AuthorityTenant,
        AuthorityTenantMembership,
    )
    from app.models.doctor import Doctor
    from app.models.synthetic_preview_appointment_fixture import (
        SYNTHETIC_PREVIEW_FIXTURE_VERSION,
        SyntheticPreviewAppointmentFixture,
    )
    from sqlmodel import select

    A, X, B = AuthorityActor, AuthorityActorExternalIdentity, AuthorityPersonBinding
    T, M = AuthorityTenant, AuthorityTenantMembership
    D, F = Doctor, SyntheticPreviewAppointmentFixture
    candidates: list[tuple[str, str]] = []
    with get_session() as session:
        rows = session.exec(
            select(
                cast(Any, X.subject),
                cast(Any, F.appointment_id),
                cast(Any, M.tenant_id),
            )
            .join(D, cast(Any, D.clerk_id) == cast(Any, X.subject))
            .join(F, cast(Any, F.doctor_id) == cast(Any, D.id))
            .join(A, cast(Any, A.id) == cast(Any, X.actor_id))
            .join(M, cast(Any, M.actor_id) == cast(Any, A.id))
            .join(T, cast(Any, T.id) == cast(Any, M.tenant_id))
            .where(
                X.issuer == AUTHORITY_KERNEL_SETTINGS.clerk_issuer,
                cast(Any, X.revoked_at).is_(None),
                A.status == "active",
                cast(Any, D.deleted_at).is_(None),
                cast(Any, D.last_active_at).is_not(None),
                M.membership_kind == "provider",
                M.status == "active",
                T.status == "active",
                cast(Any, F.synthetic_only).is_(True),
                F.fixture_version == SYNTHETIC_PREVIEW_FIXTURE_VERSION,
            )
        ).all()
        for subject, appointment_id, tenant_id in rows:
            binding = session.exec(
                select(B).where(
                    B.binding_ref == str(appointment_id),
                    B.tenant_id == tenant_id,
                    B.binding_kind == "provider_record",
                    B.status == "active",
                    cast(Any, B.revoked_at).is_(None),
                )
            ).one_or_none()
            if binding is not None:
                candidates.append((subject, binding.binding_ref))
    if len(candidates) != 1:
        raise SmokeFailure
    return candidates[0]


def _json(response, status: int) -> dict:
    if response.status_code != status:
        raise SmokeFailure
    try:
        value = response.json()
    except (TypeError, ValueError):
        raise SmokeFailure from None
    if not isinstance(value, dict):
        raise SmokeFailure
    return value


def _problem(response, status: int, code: str) -> None:
    body = _json(response, status)
    if body.get("status") != status or body.get("code") != code:
        raise SmokeFailure


def _assert_status() -> None:
    from provider_v7_acceptance import assert_status

    assert_status()


def _assert_health(client, provider_origin: str, public_origin: str) -> None:
    provider = _json(client.get(f"{provider_origin}/healthz"), 200)
    public = _json(client.get(f"{public_origin}/healthz"), 200)
    provider_expected = {
        "status": "ok",
        "mock_mode": False,
        "auth_mode": "disabled",
        "mount_profile": "provider_assistant",
        "platform_agent_api_enabled": True,
    }
    public_expected = {
        "status": "ok",
        "mock_mode": False,
        "auth_mode": "disabled",
        "mount_profile": "public",
        "platform_agent_api_enabled": False,
    }
    if any(
        provider.get(key) != value for key, value in provider_expected.items()
    ) or any(public.get(key) != value for key, value in public_expected.items()):
        raise SmokeFailure


def _persistence_snapshot(client_turn_id, answer: str) -> tuple[str, ...] | None:
    from activemodel.session_manager import get_session
    from app.models.authority import AuthorityAuditEvent, InteractionLineage
    from app.models.authority_conversation import (
        CanonicalConversationFinalPhase,
        CanonicalConversationTurn,
        CanonicalConversationUserPhase,
    )
    from app.models.authority_usage import AgentModelUsageEvent
    from sqlmodel import select

    with get_session() as session:
        lineage = session.exec(
            select(InteractionLineage).where(
                InteractionLineage.client_turn_id == client_turn_id
            )
        ).one_or_none()
        if lineage is None or "pending" in (lineage.audit_status, lineage.usage_status):
            return None
        if (lineage.audit_status, lineage.usage_status) != ("complete", "complete"):
            raise SmokeFailure
        turn = session.exec(
            select(CanonicalConversationTurn).where(
                CanonicalConversationTurn.interaction_id == lineage.id
            )
        ).one_or_none()
        if turn is None:
            return None
        users = session.exec(
            select(CanonicalConversationUserPhase).where(
                CanonicalConversationUserPhase.turn_id == turn.id
            )
        ).all()
        finals = session.exec(
            select(CanonicalConversationFinalPhase).where(
                CanonicalConversationFinalPhase.turn_id == turn.id
            )
        ).all()
        usage = session.exec(
            select(AgentModelUsageEvent).where(
                AgentModelUsageEvent.interaction_id == lineage.id
            )
        ).all()
        audits = session.exec(
            select(AuthorityAuditEvent).where(
                AuthorityAuditEvent.interaction_id == lineage.id
            )
        ).all()
    if (
        len(users) != 1
        or users[0].content != PROMPT
        or len(finals) != 1
        or finals[0].content != answer
        or not usage
        or not audits
    ):
        raise SmokeFailure
    return tuple(sorted(str(row.id) for row in (*usage, *audits)))


def _wait_for_persistence(client_turn_id, answer: str) -> tuple[str, ...]:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        snapshot = _persistence_snapshot(client_turn_id, answer)
        if snapshot is not None:
            return snapshot
        time.sleep(0.5)
    raise SmokeFailure


def run(platform_origin: str, provider_origin: str, public_origin: str) -> None:
    import httpx
    from clerk_backend_api import CreateSessionRequestBody

    from app.configuration.clerk import clerk

    _require_staging()
    clerk_subject, record_ref = _eligible_candidate()
    session_id = None
    failure: Exception | None = None
    try:
        clerk_session = clerk.sessions.create(
            request=CreateSessionRequestBody(user_id=clerk_subject)
        )
        if clerk_session is None or not clerk_session.id:
            raise SmokeFailure
        session_id = clerk_session.id
        token = clerk.sessions.create_token(session_id=session_id)
        if token is None or not token.jwt:
            raise SmokeFailure
        with httpx.Client(timeout=180, follow_redirects=False) as client:
            _assert_health(client, provider_origin, public_origin)
            headers = {"X-Cosmaneura-Embed-Version": "1.0"}
            _problem(
                client.get(f"{provider_origin}/welcome", headers=headers),
                401,
                "CONTEXT_REQUIRED",
            )
            invalid = {
                **headers,
                "Authorization": "Bearer mctx_invalid_0000000000000000",
            }
            _problem(
                client.get(f"{provider_origin}/welcome", headers=invalid),
                401,
                "CONTEXT_INVALID",
            )
            issued = _json(
                client.post(
                    f"{platform_origin}/embed/v1/context-tokens/exchange",
                    headers={**headers, "Authorization": f"Bearer {token.jwt}"},
                    json={
                        "requested_surface": "provider_assistant",
                        "record_ref": record_ref,
                    },
                ),
                201,
            )
            mount = issued.get("context_token")
            if not isinstance(mount, str) or len(mount) < 16:
                raise SmokeFailure
            provider_headers = {**headers, "Authorization": f"Bearer {mount}"}
            welcome = _json(
                client.get(f"{provider_origin}/welcome", headers=provider_headers), 200
            )
            if welcome.get("persona") != "provider" or any(
                path.get("intent") == "lab_review" for path in welcome.get("paths", [])
            ):
                raise SmokeFailure
            turn_id = uuid4()
            chat_headers = {
                **provider_headers,
                "X-Cosmaneura-Client-Turn-ID": str(turn_id),
            }
            chat = _json(
                client.post(
                    f"{provider_origin}/chat",
                    headers=chat_headers,
                    json={"client_turn_id": str(turn_id), "message": PROMPT},
                ),
                200,
            )
            answer = chat.get("answer")
            if (
                chat.get("client_turn_id") != str(turn_id)
                or not isinstance(answer, str)
                or not answer
            ):
                raise SmokeFailure
            snapshot = _wait_for_persistence(turn_id, answer)
            replay = client.post(
                f"{provider_origin}/chat",
                headers=chat_headers,
                json={"client_turn_id": str(turn_id), "message": PROMPT},
            )
            _problem(replay, 503, "SERVICE_UNAVAILABLE")
            if replay.json().get("retryable") is not False:
                raise SmokeFailure
            if _persistence_snapshot(turn_id, answer) != snapshot:
                raise SmokeFailure
    except Exception as exc:
        failure = exc
    finally:
        if session_id is not None:
            try:
                revoked = clerk.sessions.revoke(session_id=session_id)
                if revoked is None or revoked.status != "revoked":
                    failure = SmokeFailure()
            except Exception:
                failure = SmokeFailure()
    if failure is not None:
        raise SmokeFailure from None


def main() -> int:
    status_only = sys.argv[1:] in (["status"], ["--status-only"])
    try:
        _require_staging()
        _assert_status()
        if status_only:
            print("V7_STATUS PASS")
            return 0
        if len(sys.argv) != 4:
            raise SmokeFailure
        run(*sys.argv[1:])
        _assert_status()
    except Exception:
        print("V7_STATUS FAIL" if status_only else "V7_PROVIDER_SMOKE FAIL")
        return 1
    print("V7_PROVIDER_SMOKE PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
