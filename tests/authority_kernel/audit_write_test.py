from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

import app.services.authority as authority
import app.services.authority_audit as audit_service
from app.configuration.authority import AuthorityKernelSettings

from app.models.authority import (
    AuthorityActor,
    AuthorityActorExternalIdentity,
    AuthorityAuditEvent,
    AuthorityIdempotencyRecord,
    AuthorityPerson,
    AuthorityPersonBinding,
    AuthorityPersonOrigin,
    AuthorityTenant,
    AuthorityTenantMembership,
    InteractionLineage,
    MountContextToken,
)
from sqlalchemy.exc import OperationalError
from sqlmodel import Session, select

TEST_CLERK_ISSUER = "https://test.clerk.invalid"

ENABLED_SETTINGS = AuthorityKernelSettings(
    enabled=True,
    token_ttl_seconds=900,
    token_refresh_after_seconds=300,
    clerk_issuer=TEST_CLERK_ISSUER,
)
WORKLOAD_ID = "11111111-1111-4111-8111-111111111111"


def _provider_lineage(
    session: Session,
) -> tuple[str, str, AuthorityActor, AuthorityTenant, AuthorityPerson]:
    now = datetime.now(UTC)
    actor = AuthorityActor(actor_kind="human", status="active")
    tenant = AuthorityTenant(status="active")
    person = AuthorityPerson(lifecycle_state="patient", status="active")
    session.add_all([actor, tenant, person])
    session.flush()
    session.add_all(
        [
            AuthorityActorExternalIdentity(
                actor_id=actor.id,
                issuer=TEST_CLERK_ISSUER,
                subject="user_test_provider",
                verified_at=now,
            ),
            AuthorityTenantMembership(
                actor_id=actor.id,
                tenant_id=tenant.id,
                membership_kind="provider",
                status="active",
            ),
            AuthorityPersonOrigin(person_id=person.id, origin="provider_entry"),
            AuthorityPersonBinding(
                person_id=person.id,
                tenant_id=tenant.id,
                binding_ref="appointment_synthetic_001",
                binding_kind="provider_record",
                status="active",
                verified_at=now,
            ),
        ]
    )
    session.commit()
    issued = authority.exchange_context_token(
        clerk_issuer=TEST_CLERK_ISSUER,
        clerk_subject="user_test_provider",
        requested_surface="provider_assistant",
        record_ref="appointment_synthetic_001",
        session=session,
        settings=ENABLED_SETTINGS,
    )
    resolved = authority.resolve_mount_context(
        workload_client_id=WORKLOAD_ID,
        mount_context_token=issued.context_token,
        client_turn_id=uuid4(),
        session=session,
        settings=ENABLED_SETTINGS,
    )
    return issued.context_token, resolved.interaction_ref, actor, tenant, person


def _payload(person: AuthorityPerson) -> dict[str, object]:
    return {
        "client_event_id": str(uuid4()),
        "observed_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "event_type": "orchestration.completed",
        "observation_status": "completed",
        "route": "concierge",
        "intent": "care.navigation",
        "content_sha256": "a" * 64,
        "platform_refs": [{"kind": "person", "ref": str(person.id)}],
    }


def _write(
    session: Session,
    *,
    token: str | None,
    interaction_ref: str,
    payload: dict[str, object],
    key: str = "opaque-audit-key-0001",
    workload: str = WORKLOAD_ID,
) -> audit_service.AuditWriteResult:
    return audit_service.write_observational_audit_event(
        workload_client_id=workload,
        interaction_ref=interaction_ref,
        idempotency_key=key,
        payload=payload,
        mount_context_token=token,
        session=session,
        settings=ENABLED_SETTINGS,
    )


def test_audit_write_is_atomic_derived_and_exactly_replayed(
    authority_session: Session,
) -> None:
    token, interaction_ref, actor, tenant, person = _provider_lineage(authority_session)
    payload = _payload(person)

    first = _write(
        authority_session,
        token=token,
        interaction_ref=interaction_ref,
        payload=payload,
    )
    replay = _write(
        authority_session,
        token=None,
        interaction_ref=interaction_ref,
        payload=payload,
    )

    assert first.replayed is False
    assert replay.replayed is True
    assert replay.record_ref == first.record_ref
    events = authority_session.exec(
        select(AuthorityAuditEvent).where(
            AuthorityAuditEvent.event_source == "sovereign_observational"
        )
    ).all()
    assert len(events) == 1
    event = events[0]
    assert event.tenant_id == tenant.id
    assert event.actor_id == actor.id
    assert event.person_id == person.id
    assert event.request_digest is not None and len(event.request_digest) == 64
    assert event.content_sha256 == "a" * 64
    assert event.platform_refs == [{"kind": "person", "ref": str(person.id)}]
    assert len(authority_session.exec(select(AuthorityIdempotencyRecord)).all()) == 1
    lineage = authority_session.exec(
        select(InteractionLineage).where(
            InteractionLineage.interaction_ref == interaction_ref
        )
    ).one()
    # Only a terminal ``sovereign.turn.*`` observation can close lineage.
    assert lineage.audit_status == "pending"


def test_audit_write_rejects_changed_key_or_client_event_replay(
    authority_session: Session,
) -> None:
    token, interaction_ref, _, _, person = _provider_lineage(authority_session)
    payload = _payload(person)
    _write(
        authority_session,
        token=token,
        interaction_ref=interaction_ref,
        payload=payload,
    )
    changed = {**payload, "route": "referral"}

    with pytest.raises(audit_service.AuditIdempotencyConflictError):
        _write(
            authority_session,
            token=token,
            interaction_ref=interaction_ref,
            payload=changed,
        )
    with pytest.raises(audit_service.AuditIdempotencyConflictError):
        _write(
            authority_session,
            token=token,
            interaction_ref=interaction_ref,
            payload=payload,
            key="opaque-audit-key-0002",
        )


def test_audit_write_accepts_expired_matching_token_but_not_wrong_workload(
    authority_session: Session,
) -> None:
    token, interaction_ref, _, _, person = _provider_lineage(authority_session)
    stored_token = authority_session.exec(select(MountContextToken)).one()
    stored_token.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    authority_session.add(stored_token)
    authority_session.commit()

    result = _write(
        authority_session,
        token=token,
        interaction_ref=interaction_ref,
        payload=_payload(person),
    )
    assert result.replayed is False

    with pytest.raises(authority.InteractionInvalidError):
        _write(
            authority_session,
            token=None,
            interaction_ref=interaction_ref,
            payload=_payload(person),
            key="opaque-audit-key-0003",
            workload="22222222-2222-4222-8222-222222222222",
        )


def test_audit_write_rejects_unresolved_or_unsupported_platform_reference(
    authority_session: Session,
) -> None:
    token, interaction_ref, _, _, person = _provider_lineage(authority_session)
    payload = _payload(person)
    payload["platform_refs"] = [
        {"kind": "conversation_turn", "ref": "conversation_turn_missing"}
    ]

    with pytest.raises(audit_service.AuditReferenceInvalidError):
        _write(
            authority_session,
            token=token,
            interaction_ref=interaction_ref,
            payload=payload,
        )


def test_public_lineage_remains_fail_closed(authority_session: Session) -> None:
    now = datetime.now(UTC)
    actor = AuthorityActor(actor_kind="human", status="active")
    person = AuthorityPerson(lifecycle_state="patient", status="active")
    authority_session.add_all([actor, person])
    authority_session.flush()
    authority_session.add_all(
        [
            AuthorityActorExternalIdentity(
                actor_id=actor.id,
                issuer=TEST_CLERK_ISSUER,
                subject="user_test_public",
                verified_at=now,
            ),
            AuthorityPersonOrigin(person_id=person.id, origin="self_service"),
            AuthorityPersonBinding(
                person_id=person.id,
                actor_id=actor.id,
                binding_ref="record_public_001",
                binding_kind="identified_session",
                status="active",
                verified_at=now,
            ),
        ]
    )
    authority_session.commit()
    issued = authority.exchange_context_token(
        clerk_issuer=TEST_CLERK_ISSUER,
        clerk_subject="user_test_public",
        requested_surface="public",
        record_ref="record_public_001",
        session=authority_session,
        settings=ENABLED_SETTINGS,
    )
    resolved = authority.resolve_mount_context(
        workload_client_id=WORKLOAD_ID,
        mount_context_token=issued.context_token,
        client_turn_id=uuid4(),
        session=authority_session,
        settings=ENABLED_SETTINGS,
    )

    with pytest.raises(authority.AuthorityRequestDeniedError):
        _write(
            authority_session,
            token=issued.context_token,
            interaction_ref=resolved.interaction_ref,
            payload=_payload(person),
        )


def test_audit_write_rolls_back_event_receipt_and_ack_together(
    authority_session: Session,
    monkeypatch,
) -> None:
    token, interaction_ref, _, _, person = _provider_lineage(authority_session)
    original_commit = authority_session.commit

    def fail_commit() -> None:
        raise OperationalError("COMMIT", {}, RuntimeError("synthetic"))

    monkeypatch.setattr(authority_session, "commit", fail_commit)
    with pytest.raises(OperationalError):
        _write(
            authority_session,
            token=token,
            interaction_ref=interaction_ref,
            payload=_payload(person),
        )
    authority_session.rollback()
    monkeypatch.setattr(authority_session, "commit", original_commit)

    assert not authority_session.exec(
        select(AuthorityAuditEvent).where(
            AuthorityAuditEvent.event_source == "sovereign_observational"
        )
    ).all()
    assert not authority_session.exec(select(AuthorityIdempotencyRecord)).all()
    lineage = authority_session.exec(
        select(InteractionLineage).where(
            InteractionLineage.interaction_ref == interaction_ref
        )
    ).one()
    assert lineage.audit_status == "pending"
