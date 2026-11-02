"""Explicit, hash-confirmed NULL-practice correction; never a runtime fallback.

The original grant remains evidence. Its complete revocation, one successor,
and a distinct append-only binding receipt commit atomically. No identity,
membership, BAA, billing, global setting or clinical record is changed.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

from app.configuration.authority import AUTHORITY_KERNEL_SETTINGS
from app.environments import python_environment
from app.services.pilot_access_lock import lock_pilot_access_lineage

from activemodel.session_manager import get_session
from activemodel.types import TypeIDType
from app.models.authority import AuthorityAuditEvent
from app.models.doctor import Doctor
from app.models.pilot_access_continuity_amendment import (
    PilotAccessContinuityAmendmentIdentity,
)
from app.models.pilot_access_grandfather_grant import PilotAccessGrandfatherGrant
from app.models.pilot_access_practice_binding import PilotAccessPracticeBinding
from app.models.practice import (
    SEAT_HOLDING_ACCESS_STATES,
    Practice,
    PracticeMember,
    member_access_state,
)
from app.models.practice_access_audit import PracticeAccessAction, PracticeAccessAudit
from sqlalchemy import func, or_
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, col, select

ACTION = "pilot_practice_snapshot_bound"
MAX_PLAN_AGE_SECONDS = 900


class PilotPracticeBindingError(ValueError):
    """Fixed refusals contain no provider identity or chart information."""


@dataclass(frozen=True)
class BindingRequest:
    doctor_id: TypeIDType
    practice_id: TypeIDType
    predecessor_grant_id: TypeIDType
    identity_sha256: str
    email_sha256: str
    before_state_sha256: str
    source_commit_sha: str
    observed_at: datetime
    operator_ref: str
    reason: str


@dataclass(frozen=True)
class BindingPlan:
    plan_sha256: str
    already_applied: bool
    environment: str


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise PilotPracticeBindingError("timezone-aware timestamp required")
    return value.astimezone(UTC)


def _retention(value: datetime) -> datetime:
    try:
        return value.replace(year=value.year + 7)
    except ValueError:
        return value.replace(year=value.year + 7, month=3, day=1)


def grant_before_state_sha256(grant: PilotAccessGrandfatherGrant) -> str:
    """Same closed before-state projection as the qualified NULL diagnostic."""
    return _sha(
        json.dumps(
            [
                str(grant.id),
                str(grant.doctor_id_snapshot) if grant.doctor_id_snapshot else None,
                str(grant.practice_id_snapshot) if grant.practice_id_snapshot else None,
                grant.email_sha256,
                grant.grant_plan_sha256,
                _utc(grant.granted_at).isoformat(),
                None,
            ],
            separators=(",", ":"),
        )
    )


def _original_evidence_sha256(grant: PilotAccessGrandfatherGrant) -> str:
    return _sha(
        _canonical(
            [
                grant.source_ref,
                grant.source_sha256,
                grant.granted_by_ref,
                grant.grant_reason,
            ]
        )
    )


def _request_payload(
    request: BindingRequest, environment: str, issuer_sha: str
) -> dict[str, Any]:
    for value in (
        request.identity_sha256,
        request.email_sha256,
        request.before_state_sha256,
    ):
        if re.fullmatch(r"[0-9a-f]{64}", value) is None:
            raise PilotPracticeBindingError("invalid confirmation digest")
    if re.fullmatch(r"[0-9a-f]{40}", request.source_commit_sha) is None:
        raise PilotPracticeBindingError("invalid source commit")
    for value, limit in ((request.operator_ref, 255), (request.reason, 512)):
        if not value or value != value.strip() or len(value) > limit:
            raise PilotPracticeBindingError("invalid operation reference")
    return {
        "schema_version": 1,
        "action": ACTION,
        "environment": environment,
        "issuer_sha256": issuer_sha,
        "source_commit_sha": request.source_commit_sha,
        "doctor_id": str(request.doctor_id),
        "practice_id": str(request.practice_id),
        "predecessor_grant_id": str(request.predecessor_grant_id),
        "identity_sha256": request.identity_sha256,
        "email_sha256": request.email_sha256,
        "before_state_sha256": request.before_state_sha256,
        "observed_at_UTC": _utc(request.observed_at).isoformat(),
        "operator_ref": request.operator_ref,
        "reason": request.reason,
    }


def binding_evidence_valid(
    binding: PilotAccessPracticeBinding,
    predecessor: PilotAccessGrandfatherGrant,
    successor: PilotAccessGrandfatherGrant,
    receipt: PracticeAccessAudit,
    *,
    environment: str | None,
    issuer_sha256: str | None,
) -> bool:
    """Verify retained lineage independently of today's membership eligibility."""
    try:
        p = json.loads(binding.payload_json)
        required = {
            "schema_version",
            "action",
            "environment",
            "issuer_sha256",
            "source_commit_sha",
            "doctor_id",
            "practice_id",
            "predecessor_grant_id",
            "identity_sha256",
            "email_sha256",
            "before_state_sha256",
            "observed_at_UTC",
            "operator_ref",
            "reason",
            "eligibility_sha256",
            "original_evidence_sha256",
        }
        if (
            not isinstance(p, dict)
            or set(p) != required
            or _canonical(p) != binding.payload_json
        ):
            return False
        observed = _utc(datetime.fromisoformat(p["observed_at_UTC"]))
        applied = _utc(binding.applied_at)
        hashes = (
            p["issuer_sha256"],
            p["identity_sha256"],
            p["email_sha256"],
            p["before_state_sha256"],
            p["eligibility_sha256"],
            p["original_evidence_sha256"],
        )
        return bool(
            all(isinstance(h, str) and re.fullmatch(r"[0-9a-f]{64}", h) for h in hashes)
            and re.fullmatch(r"[0-9a-f]{40}", p["source_commit_sha"])
            and type(p["schema_version"]) is int
            and p["schema_version"] == 1
            and p["action"] == ACTION
            and p["environment"] == binding.environment
            and binding.environment in {"staging", "production"}
            and (environment is None or binding.environment == environment)
            and (issuer_sha256 is None or p["issuer_sha256"] == issuer_sha256)
            and _sha(binding.payload_json) == binding.plan_sha256
            and binding.predecessor_grant_id == predecessor.id
            and binding.successor_grant_id == successor.id
            and predecessor.id != successor.id
            and binding.receipt_id == receipt.id
            and p["predecessor_grant_id"] == str(predecessor.id)
            and p["before_state_sha256"] == grant_before_state_sha256(predecessor)
            and p["original_evidence_sha256"] == _original_evidence_sha256(predecessor)
            and p["doctor_id"] == str(successor.doctor_id_snapshot)
            and p["practice_id"] == str(successor.practice_id_snapshot)
            and successor.doctor_id_snapshot is not None
            and successor.practice_id_snapshot is not None
            and predecessor.doctor_id_snapshot in (None, successor.doctor_id_snapshot)
            and predecessor.practice_id_snapshot is None
            and predecessor.clerk_id == successor.clerk_id
            and predecessor.email_snapshot == successor.email_snapshot
            and predecessor.email_sha256 == successor.email_sha256 == p["email_sha256"]
            and successor.email_sha256 == _sha(successor.email_snapshot)
            and p["identity_sha256"]
            == _sha(
                json.dumps(
                    [
                        str(successor.doctor_id_snapshot),
                        successor.clerk_id,
                        successor.email_snapshot,
                    ],
                    separators=(",", ":"),
                )
            )
            and predecessor.source_ref == successor.source_ref
            and predecessor.source_sha256 == successor.source_sha256
            and predecessor.revocation_plan_sha256
            == successor.grant_plan_sha256
            == binding.plan_sha256
            and predecessor.revoked_at == successor.granted_at == binding.applied_at
            and predecessor.revoked_at is not None
            and predecessor.granted_at <= predecessor.revoked_at
            and predecessor.retain_until is not None
            and _utc(predecessor.retain_until) >= _retention(applied)
            and predecessor.revoked_by_ref
            == successor.granted_by_ref
            == receipt.operator_ref
            == p["operator_ref"]
            and predecessor.revocation_reason
            == successor.grant_reason
            == receipt.reason
            == p["reason"]
            and receipt.action == ACTION
            and receipt.practice_id == successor.practice_id_snapshot
            and receipt.evidence_sha256 == binding.plan_sha256
            and receipt.document_reference == f"pilot-practice-binding:{predecessor.id}"
            and receipt.effective_at == binding.applied_at
            and receipt.expires_at is None
            and 0 <= (applied - observed).total_seconds() <= MAX_PLAN_AGE_SECONDS
        )
    except (ValueError, TypeError, KeyError, AttributeError):
        return False


def _row(session: Session, statement: Any, lock: bool) -> Any:
    statement = statement.execution_options(populate_existing=True)
    if lock:
        statement = statement.with_for_update()
    return session.exec(statement).one_or_none()


def _require_current_provider(
    doctor: Any, member: Any, practice: Any, current: datetime
) -> None:
    if (
        practice is None
        or doctor is None
        or member is None
        or doctor.deleted_at is not None
        or doctor.onboarding_completed_at is None
        or not doctor.email
        or not doctor.clerk_id
        or not doctor.clerk_id.startswith("user_")
        or member.practice_id != practice.id
        or member.doctor_id != doctor.id
        or practice.organization_id is not None
        or member.access_revoked_at is not None
        or member.attested_at is None
        or _utc(member.attested_at) > current
        or _utc(doctor.onboarding_completed_at) > current
        or member_access_state(member, at=current) not in SEAT_HOLDING_ACCESS_STATES
        or not practice.baa_in_force(at=current)
    ):
        raise PilotPracticeBindingError(
            "current provider practice prerequisites not satisfied"
        )


def _assert_closed_cohort(session: Session, environment: str) -> None:
    # Local import keeps the audit's pure binding verifier free of a cycle.
    from app.services.pilot_access_audit import audit_pilot_access_state

    if not audit_pilot_access_state(
        session=session,
        expected_environment=environment,
        expected_clerk_issuer=AUTHORITY_KERNEL_SETTINGS.clerk_issuer,
    ).valid:
        raise PilotPracticeBindingError(
            "existing closed pilot cohort integrity is invalid"
        )


def _require_clean_session(session: Session) -> None:
    if session.new or session.dirty or session.deleted:
        raise PilotPracticeBindingError(
            "binding requires a clean dedicated transaction"
        )


def _evaluate(
    session: Session, request: BindingRequest, *, lock: bool
) -> tuple[BindingPlan, dict[str, Any], Any, Any]:
    environment = python_environment()
    if (
        environment not in {"staging", "production"}
        or not AUTHORITY_KERNEL_SETTINGS.has_valid_clerk_issuer()
    ):
        raise PilotPracticeBindingError("configured runtime provenance required")
    issuer_sha = _sha(AUTHORITY_KERNEL_SETTINGS.clerk_issuer)
    payload = _request_payload(request, environment, issuer_sha)
    # Same order as current practice-member revocation; refresh after lock waits.
    practice = _row(
        session, select(Practice).where(Practice.id == request.practice_id), lock
    )
    doctor = _row(session, select(Doctor).where(Doctor.id == request.doctor_id), lock)
    member = _row(
        session,
        select(PracticeMember).where(PracticeMember.doctor_id == request.doctor_id),
        lock,
    )
    current = datetime.now(UTC)
    _require_current_provider(doctor, member, practice, current)
    email = doctor.email.strip().lower()
    identity = _sha(
        json.dumps([str(doctor.id), doctor.clerk_id, email], separators=(",", ":"))
    )
    if (
        not hmac.compare_digest(identity, request.identity_sha256)
        or _sha(email) != request.email_sha256
    ):
        raise PilotPracticeBindingError("current identity confirmation changed")
    owner_count = session.exec(
        select(col(Doctor.id)).where(
            or_(
                col(Doctor.clerk_id) == doctor.clerk_id,
                func.lower(func.btrim(Doctor.email)) == email,
            )
        )
    ).all()
    if len(owner_count) != 1:
        raise PilotPracticeBindingError("current identity is ambiguous")
    grants_query = (
        select(PilotAccessGrandfatherGrant)
        .where(
            or_(
                col(PilotAccessGrandfatherGrant.clerk_id) == doctor.clerk_id,
                col(PilotAccessGrandfatherGrant.doctor_id_snapshot) == doctor.id,
                col(PilotAccessGrandfatherGrant.email_sha256) == request.email_sha256,
            )
        )
        .execution_options(populate_existing=True)
    )
    grants = list(
        session.exec(grants_query.with_for_update() if lock else grants_query).all()
    )
    predecessor = next(
        (g for g in grants if g.id == request.predecessor_grant_id), None
    )
    binding = _row(
        session,
        select(PilotAccessPracticeBinding).where(
            PilotAccessPracticeBinding.predecessor_grant_id
            == request.predecessor_grant_id
        ),
        lock,
    )
    if binding is not None:
        successor = next(
            (g for g in grants if g.id == binding.successor_grant_id), None
        )
        receipt = _row(
            session,
            select(PracticeAccessAudit).where(
                PracticeAccessAudit.id == binding.receipt_id
            ),
            lock,
        )
        current = datetime.now(UTC)
        _require_current_provider(doctor, member, practice, current)
        if (
            predecessor is None
            or successor is None
            or receipt is None
            or len(grants) != 2
            or successor.revoked_at is not None
            or _utc(successor.granted_at) > current
            or not binding_evidence_valid(
                binding,
                predecessor,
                successor,
                receipt,
                environment=environment,
                issuer_sha256=issuer_sha,
            )
        ):
            raise PilotPracticeBindingError(
                "prior binding evidence or current grant is invalid"
            )
        prior = json.loads(binding.payload_json)
        if any(prior.get(k) != v for k, v in payload.items()):
            raise PilotPracticeBindingError("retry does not match original binding")
        return (
            BindingPlan(binding.plan_sha256, True, environment),
            prior,
            predecessor,
            current,
        )
    current = datetime.now(UTC)
    _require_current_provider(doctor, member, practice, current)
    if (
        predecessor is None
        or len(grants) != 1
        or predecessor.revoked_at is not None
        or _utc(predecessor.granted_at) > current
        or predecessor.practice_id_snapshot is not None
        or predecessor.doctor_id_snapshot not in (None, doctor.id)
        or predecessor.clerk_id != doctor.clerk_id
        or predecessor.email_snapshot != email
        or predecessor.email_sha256 != request.email_sha256
        or re.fullmatch(r"[0-9a-f]{64}", predecessor.source_sha256) is None
        or re.fullmatch(r"[0-9a-f]{64}", predecessor.grant_plan_sha256) is None
        or any(
            getattr(predecessor, k) is not None
            for k in (
                "revoked_by_ref",
                "revocation_reason",
                "revocation_plan_sha256",
                "retain_until",
            )
        )
        or grant_before_state_sha256(predecessor) != request.before_state_sha256
    ):
        raise PilotPracticeBindingError("singular unrevoked NULL legacy grant required")
    if (
        session.exec(
            select(col(PilotAccessContinuityAmendmentIdentity.id)).where(
                PilotAccessContinuityAmendmentIdentity.grant_id == predecessor.id
            )
        ).first()
        is not None
        or session.exec(
            select(col(AuthorityAuditEvent.id)).where(
                AuthorityAuditEvent.request_digest == predecessor.grant_plan_sha256,
                AuthorityAuditEvent.event_type == "platform.doctor_identity.rebound",
            )
        ).first()
        is not None
    ):
        raise PilotPracticeBindingError(
            "linked authorization lineage requires separate review"
        )
    if (
        not 0
        <= (current - _utc(request.observed_at)).total_seconds()
        <= MAX_PLAN_AGE_SECONDS
    ):
        raise PilotPracticeBindingError("observation is expired or future-dated")
    eligibility = [
        str(doctor.id),
        str(member.id),
        str(practice.id),
        _utc(doctor.onboarding_completed_at).isoformat(),
        _utc(member.attested_at).isoformat(),
        member_access_state(member, at=current),
        str(member.access_ends_at),
        practice.baa_version,
        str(practice.baa_accepted_at),
        practice.negotiated_baa_on_file,
        str(practice.negotiated_baa_effective_date),
        practice.negotiated_baa_reference,
    ]
    payload["eligibility_sha256"] = _sha(_canonical(eligibility))
    payload["original_evidence_sha256"] = _original_evidence_sha256(predecessor)
    return (
        BindingPlan(_sha(_canonical(payload)), False, environment),
        payload,
        predecessor,
        current,
    )


def plan_pilot_practice_binding(
    request: BindingRequest, *, session: Session | None = None
) -> BindingPlan:
    """Validate current state without writing; only the digest is public output."""
    if session is None:
        with get_session() as managed:
            return plan_pilot_practice_binding(request, session=cast(Session, managed))
    _require_clean_session(session)
    try:
        plan = _evaluate(session, request, lock=False)[0]
        _assert_closed_cohort(session, plan.environment)
        return plan
    except SQLAlchemyError:
        raise PilotPracticeBindingError("binding validation unavailable") from None


def apply_pilot_practice_binding(
    request: BindingRequest,
    *,
    confirmed_plan_sha256: str,
    session: Session | None = None,
) -> BindingPlan:
    """CAS the reviewed state after transaction-wide lineage and row locks."""
    if session is None:
        with get_session() as managed:
            return apply_pilot_practice_binding(
                request,
                confirmed_plan_sha256=confirmed_plan_sha256,
                session=cast(Session, managed),
            )
    # Refuse before SQL/autoflush or rollback can publish/discard unrelated work.
    _require_clean_session(session)
    try:
        if re.fullmatch(r"[0-9a-f]{64}", confirmed_plan_sha256) is None:
            raise PilotPracticeBindingError("invalid confirmed plan digest")
        lock_pilot_access_lineage(session)
        plan, payload, predecessor, current = _evaluate(session, request, lock=True)
        if not hmac.compare_digest(plan.plan_sha256, confirmed_plan_sha256):
            raise PilotPracticeBindingError(
                "confirmed plan no longer matches current state"
            )
        _assert_closed_cohort(session, plan.environment)
        if plan.already_applied:
            session.commit()
            return plan
        predecessor.revoked_at = current
        predecessor.revoked_by_ref = request.operator_ref
        predecessor.revocation_reason = request.reason
        predecessor.revocation_plan_sha256 = plan.plan_sha256
        predecessor.retain_until = _retention(current)
        session.add(predecessor)
        session.flush()  # Release the three partial active-grant unique indexes first.
        successor = PilotAccessGrandfatherGrant(
            clerk_id=predecessor.clerk_id,
            doctor_id_snapshot=request.doctor_id,
            practice_id_snapshot=request.practice_id,
            email_snapshot=predecessor.email_snapshot,
            email_sha256=predecessor.email_sha256,
            source_ref=predecessor.source_ref,
            source_sha256=predecessor.source_sha256,
            grant_plan_sha256=plan.plan_sha256,
            granted_by_ref=request.operator_ref,
            grant_reason=request.reason,
            granted_at=current,
        )
        receipt = PracticeAccessAudit(
            practice_id=request.practice_id,
            action=PracticeAccessAction.pilot_practice_snapshot_bound.value,
            operator_ref=request.operator_ref,
            reason=request.reason,
            effective_at=current,
            document_reference=f"pilot-practice-binding:{predecessor.id}",
            evidence_sha256=plan.plan_sha256,
        )
        session.add(successor)
        session.add(receipt)
        session.flush()
        binding = PilotAccessPracticeBinding(
            predecessor_grant_id=predecessor.id,
            successor_grant_id=successor.id,
            receipt_id=receipt.id,
            environment=plan.environment,
            plan_sha256=plan.plan_sha256,
            payload_json=_canonical(payload),
            applied_at=current,
        )
        if not binding_evidence_valid(
            binding,
            predecessor,
            successor,
            receipt,
            environment=plan.environment,
            issuer_sha256=payload["issuer_sha256"],
        ):
            raise PilotPracticeBindingError("new binding evidence failed validation")
        session.add(binding)
        session.flush()
        _assert_closed_cohort(session, plan.environment)
        session.commit()
        return plan
    except (PilotPracticeBindingError, SQLAlchemyError) as error:
        session.rollback()
        if isinstance(error, PilotPracticeBindingError):
            raise
        raise PilotPracticeBindingError(
            "concurrent or invalid binding state prevented commit"
        ) from None
