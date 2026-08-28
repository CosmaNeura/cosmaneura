"""Atomic privileged changes to practice entitlement and BAA fallback state."""

from datetime import datetime, timezone

from activemodel.session_manager import get_session
from app.models.practice import Practice
from app.models.practice_access_audit import (
    PracticeAccessAction,
    PracticeAccessAudit,
)
from sqlmodel import Session, select


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

    with get_session() as session:
        _attach(session, practice)
        # The caller may still hold a stale instance in this session's identity
        # map. Force the locking SELECT to overwrite it so a Checkout request
        # frozen by another transaction cannot be hidden by cached attributes.
        locked = session.exec(
            select(Practice)
            .where(Practice.id == practice.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).one()
        if not clear:
            from app.services import stripe_billing

            if (
                locked.stripe_checkout_request_json is not None
                or locked.stripe_checkout_session_id is not None
            ):
                raise PracticeAccessChangeError(
                    "an existing Stripe checkout must be resolved before setting "
                    "a billing exemption"
                )
            if stripe_billing.has_nonterminal_subscription(locked):
                raise PracticeAccessChangeError(
                    "an existing Stripe subscription must be resolved before "
                    "setting a billing exemption"
                )
            locked.billing_exempt = True
            locked.billing_exempt_until = expires_at
            action = PracticeAccessAction.billing_exemption_set
            audit_expiry = expires_at
        else:
            locked.billing_exempt = False
            locked.billing_exempt_until = None
            action = PracticeAccessAction.billing_exemption_cleared
            audit_expiry = None

        audit = PracticeAccessAudit(
            practice_id=locked.id,
            action=action.value,
            operator_ref=operator,
            reason=explanation,
            effective_at=current,
            expires_at=audit_expiry,
        )
        session.add(locked)
        session.add(audit)
        session.commit()
        session.refresh(locked)
        session.refresh(audit)
    return audit


def record_negotiated_baa(
    practice: Practice,
    *,
    operator_ref: str,
    reason: str,
    document_reference: str,
    document_sha256: str | None = None,
    effective_at: datetime,
) -> PracticeAccessAudit:
    """Record a negotiated BAA and append immutable operator evidence.

    Future-dated agreements are intentionally allowed for advance setup, but
    ``Practice.baa_in_force`` keeps PHI locked until ``effective_at``.
    """

    operator = _required(operator_ref, field="operator_ref", limit=255)
    explanation = _required(reason, field="reason", limit=512)
    reference = _required(document_reference, field="document_reference", limit=255)
    digest = document_sha256.strip().lower() if document_sha256 else None
    if digest is not None and (
        len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest)
    ):
        raise PracticeAccessChangeError(
            "document_sha256 must be a 64-character hex digest"
        )
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
        evidence_sha256=digest,
    )
    with get_session() as session:
        _attach(session, practice)
        session.add(audit)
        session.commit()
        session.refresh(practice)
        session.refresh(audit)
    return audit
