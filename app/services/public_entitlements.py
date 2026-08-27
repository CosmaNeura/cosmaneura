"""Canonical public-provider capability and active-seat policy.

This module is intentionally the shared in-process boundary for billing and
membership workflows. A browser or downstream service must never derive an
authoritative seat quantity or capability grant independently.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.models.practice import ClinicianSeatState, Practice, PracticeMember
from sqlalchemy import func, or_
from sqlmodel import Session, col, select

PUBLIC_ENTITLEMENT_CONTRACT_VERSION = "public-capability-entitlements.v1"
PUBLIC_ENTITLEMENT_POLICY_REVISION = "2026-08-27"
MAX_ACTIVE_CLINICIAN_SEATS = 20

CAPABILITY_KEYS = (
    "soap_note",
    "intake_prep",
    "session_prep",
    "scribe",
    "cab",
)

CREDENTIAL_STATES = (
    "pending",
    "approved",
    "rejected",
    "needs_review",
)

FAIL_CLOSED_SUBSCRIPTION_STATES = frozenset({"pending_verification", "invalid"})
KNOWN_SUBSCRIPTION_STATES = frozenset(
    {
        "incomplete",
        "incomplete_expired",
        "pending_verification",
        "invalid",
        "trialing",
        "active",
        "past_due",
        "unpaid",
        "paused",
        "canceled",
    }
)


class ClinicianSeatPolicyError(RuntimeError):
    """Core cannot safely evaluate or mutate a practice's seat ledger."""


class ActiveClinicianSeatLimitReached(ClinicianSeatPolicyError):
    """An activation would exceed the public paid-pilot seat ceiling."""

    def __init__(self) -> None:
        super().__init__(
            f"A practice may have at most {MAX_ACTIVE_CLINICIAN_SEATS} "
            "active clinicians."
        )


def public_subscription_in_force(
    practice: Practice,
    *,
    at: datetime | None = None,
) -> bool:
    """Resolve the public subscription gate without trusting stale trial data.

    A legacy BAA-time ``trial_ends_at`` value is not enough by itself during
    the receiver-first rollout.  Unknown and verification-failure states deny
    even if an older entitlement timestamp remains present.
    """

    status = practice.subscription_status
    if status is not None and status not in KNOWN_SUBSCRIPTION_STATES:
        return False
    if status in FAIL_CLOSED_SUBSCRIPTION_STATES:
        return False
    if practice.billing_exemption_active(at=at):
        return True
    if status == "active":
        return True
    if status == "trialing":
        current = at or datetime.now(timezone.utc)
        return bool(practice.trial_ends_at and practice.trial_ends_at > current)
    if status is not None:
        return False
    # Creating a hosted Checkout Session starts the billing clock, but it does
    # not prove that card collection completed. The verified Stripe
    # subscription projection is the only paid-path entitlement source.
    return False


def lock_practice_for_active_seat_mutation(
    session: Session,
    practice_id: Any,
) -> Practice:
    """Take the serialization lock required by every seat activation.

    This must be the first durable row lock taken by a membership mutation.
    Callers may then lock the doctor, invitation, or roster evidence before
    invoking :func:`require_active_clinician_seat_capacity` immediately before
    the activation is flushed.
    """

    practice = session.exec(
        select(Practice).where(Practice.id == practice_id).with_for_update()
    ).one_or_none()
    if practice is None:
        raise ClinicianSeatPolicyError("practice seat ledger is unavailable")
    return practice


def active_clinician_seat_count(session: Session, practice_id: Any) -> int:
    """Return Core's authoritative paid-seat quantity for one practice.

    Pending invitations have no membership row and therefore do not count.
    A reversible inactive seat and an irreversible access-revocation tombstone
    both stop occupancy. Callers that mutate membership must serialize on the
    Practice row before using this result to enforce the maximum.
    """

    invalid_state = session.exec(
        select(col(PracticeMember.id))
        .where(
            PracticeMember.practice_id == practice_id,
            or_(
                col(PracticeMember.seat_state).is_(None),
                col(PracticeMember.seat_state).not_in(
                    tuple(state.value for state in ClinicianSeatState)
                ),
            ),
        )
        .limit(1)
    ).first()
    if invalid_state is not None:
        # Never undercount a corrupt or forward-incompatible state precisely
        # when deciding whether another clinician may be activated.
        raise ClinicianSeatPolicyError("practice seat ledger has an invalid state")

    count = session.exec(
        select(func.count())
        .select_from(PracticeMember)
        .where(
            PracticeMember.practice_id == practice_id,
            PracticeMember.seat_state == ClinicianSeatState.active.value,
            col(PracticeMember.access_revoked_at).is_(None),
        )
    ).one()
    return int(count)


def require_active_clinician_seat_capacity(
    session: Session,
    locked_practice: Practice,
) -> int:
    """Reject an additional activation unless the locked ledger has room.

    ``locked_practice`` must come from
    :func:`lock_practice_for_active_seat_mutation` in this transaction. The
    returned value is the count before the caller's pending activation.
    """

    count = active_clinician_seat_count(session, locked_practice.id)
    if count >= MAX_ACTIVE_CLINICIAN_SEATS:
        raise ActiveClinicianSeatLimitReached
    return count


def record_active_clinician_seat_quantity(
    session: Session,
    locked_practice: Practice,
) -> int:
    """Persist Stripe's repair obligation in the seat mutation transaction.

    The periodic billing reconciler performs remote I/O later.  Persisting the
    canonical target here closes the crash window between a membership commit
    and a second transaction that would otherwise enqueue reconciliation.
    """

    session.flush()
    count = active_clinician_seat_count(session, locked_practice.id)
    if count > MAX_ACTIVE_CLINICIAN_SEATS:
        raise ActiveClinicianSeatLimitReached
    if locked_practice.stripe_subscription_id:
        locked_practice.stripe_quantity_sync_required = True
        locked_practice.stripe_quantity_sync_target = count
        locked_practice.stripe_quantity_sync_version += 1
        locked_practice.stripe_quantity_sync_error = None
        session.add(locked_practice)
    return count


def activate_existing_clinician_seat(
    session: Session,
    locked_practice: Practice,
    member: PracticeMember,
) -> bool:
    """Reactivate one existing member under the same serialized cap.

    Returns ``False`` for an already-active idempotent retry. Revoked,
    cross-practice, or unknown membership state is rejected fail closed.
    """

    if member.practice_id != locked_practice.id:
        raise ClinicianSeatPolicyError("membership does not belong to practice")
    if member.access_revoked_at is not None:
        raise ClinicianSeatPolicyError("revoked membership cannot be reactivated")
    if member.seat_state == ClinicianSeatState.active.value:
        return False
    if member.seat_state != ClinicianSeatState.inactive.value:
        raise ClinicianSeatPolicyError("membership has an invalid seat state")

    require_active_clinician_seat_capacity(session, locked_practice)
    member.seat_state = ClinicianSeatState.active.value
    session.add(member)
    return True
