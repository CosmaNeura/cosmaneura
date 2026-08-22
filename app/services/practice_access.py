"""Atomic privileged changes to practice entitlement and BAA fallback state."""

from datetime import datetime, timezone

from activemodel.session_manager import get_session
from app.models.practice import Practice
from app.models.practice_access_audit import (
    PracticeAccessAction,
    PracticeAccessAudit,
)
from sqlmodel import Session


class PracticeAccessChangeError(ValueError):
    pass


def _required(value: str, *, field: str, limit: int) -> str:
    normalized = value.strip()
    if not normalized:
        raise PracticeAccessChangeError(f"{field} is required")
    if len(normalized) > limit:
        raise PracticeAccessChangeError(f"{field} exceeds {limit} characters")
    return normalized


def _attach(session: Session, practice: Practice) -> None:
    old_session = Session.object_session(practice)
    if old_session is not None and old_session is not session:
        old_session.expunge(practice)
    session.add(practice)


def set_billing_exemption(
    practice: Practice,
    *,
    operator_ref: str,
    reason: str,
    expires_at: datetime | None,
    clear: bool = False,
    now: datetime | None = None,
) -> PracticeAccessAudit:
    """Change a comp and append operator evidence in the same transaction."""

    operator = _required(operator_ref, field="operator_ref", limit=255)
    explanation = _required(reason, field="reason", limit=512)
    current = now or datetime.now(timezone.utc)
    if not clear and expires_at is not None and expires_at <= current:
        raise PracticeAccessChangeError(
            "billing exemption expiry must be in the future"
        )

    if clear:
        practice.billing_exempt = False
        practice.billing_exempt_until = None
        action = PracticeAccessAction.billing_exemption_cleared
        audit_expiry = None
    else:
        practice.billing_exempt = True
        practice.billing_exempt_until = expires_at
        action = PracticeAccessAction.billing_exemption_set
        audit_expiry = expires_at

    audit = PracticeAccessAudit(
        practice_id=practice.id,
        action=action.value,
        operator_ref=operator,
        reason=explanation,
        effective_at=current,
        expires_at=audit_expiry,
    )
    with get_session() as session:
        _attach(session, practice)
        session.add(audit)
        session.commit()
        session.refresh(practice)
        session.refresh(audit)
    return audit


def record_negotiated_baa(
    practice: Practice,
    *,
    operator_ref: str,
    reason: str,
    document_reference: str,
    effective_at: datetime,
) -> PracticeAccessAudit:
    """Record a negotiated BAA and append immutable operator evidence.

    Future-dated agreements are intentionally allowed for advance setup, but
    ``Practice.baa_in_force`` keeps PHI locked until ``effective_at``.
    """

    operator = _required(operator_ref, field="operator_ref", limit=255)
    explanation = _required(reason, field="reason", limit=512)
    reference = _required(document_reference, field="document_reference", limit=255)
    if effective_at.tzinfo is None:
        raise PracticeAccessChangeError("effective_at must include a timezone")
    current = datetime.now(timezone.utc)
    if (
        practice.negotiated_baa_on_file
        and practice.baa_in_force(at=current)
        and effective_at > current
    ):
        raise PracticeAccessChangeError(
            "a future BAA cannot replace the negotiated BAA currently in force"
        )

    practice.negotiated_baa_on_file = True
    practice.negotiated_baa_effective_date = effective_at
    practice.negotiated_baa_reference = reference
    audit = PracticeAccessAudit(
        practice_id=practice.id,
        action=PracticeAccessAction.negotiated_baa_recorded.value,
        operator_ref=operator,
        reason=explanation,
        effective_at=effective_at,
        document_reference=reference,
    )
    with get_session() as session:
        _attach(session, practice)
        session.add(audit)
        session.commit()
        session.refresh(practice)
        session.refresh(audit)
    return audit
