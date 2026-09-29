"""Canonical public-provider capability and active-seat policy.

This module is intentionally the shared in-process boundary for billing and
membership workflows. A browser or downstream service must never derive an
authoritative seat quantity or capability grant independently.
"""

from __future__ import annotations

from typing import Any

from app.models.practice import (
    ClinicianSeatState,
    Practice,
    PracticeMember,
    lock_practice_seats,
    practice_seat_lock_held,
)
from sqlalchemy import func, or_
from sqlmodel import Session, col, select

PUBLIC_ENTITLEMENT_CONTRACT_VERSION = "public-capability-entitlements.v1"
PUBLIC_ENTITLEMENT_POLICY_REVISION = "2026-08-28"
MAX_ACTIVE_CLINICIAN_SEATS = 20

CAPABILITY_KEYS = (
    "soap_note",
    "intake_prep",
    "session_prep",
    "scribe",
    "cab",
)

FAIL_CLOSED_SUBSCRIPTION_STATES = frozenset({"pending_verification", "invalid"})


class ClinicianSeatPolicyError(RuntimeError):
    """Core cannot safely evaluate a practice's active-seat ledger."""


class ActiveClinicianSeatLimitReached(ClinicianSeatPolicyError):
    """An activation would exceed the public paid-pilot seat ceiling."""

    def __init__(self) -> None:
        super().__init__(
            f"A practice may have at most {MAX_ACTIVE_CLINICIAN_SEATS} "
            "active clinicians."
        )


def lock_practice_for_active_seat_mutation(
    session: Session,
    practice_id: Any,
) -> Practice:
    """Serialize every active-seat decision on the owning practice."""

    practice = lock_practice_seats(session, practice_id)
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
        # Never undercount a corrupt or forward-incompatible state while
        # deriving the quantity that Stripe is allowed to mirror.
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
    """Reject an activation unless the locked ledger has room."""

    count = active_clinician_seat_count(session, locked_practice.id)
    if count >= MAX_ACTIVE_CLINICIAN_SEATS:
        raise ActiveClinicianSeatLimitReached
    return count


def record_active_clinician_seat_quantity(
    session: Session,
    locked_practice: Practice,
) -> int:
    """Persist the exact Stripe quantity obligation in the seat transaction."""

    if not practice_seat_lock_held(session, locked_practice.id):
        raise ClinicianSeatPolicyError("practice seat ledger is not locked")
    session.flush()
    count = active_clinician_seat_count(session, locked_practice.id)
    if not 1 <= count <= MAX_ACTIVE_CLINICIAN_SEATS:
        raise ClinicianSeatPolicyError(
            "active clinician count is outside the paid-pilot range"
        )
    if locked_practice.stripe_subscription_id:
        locked_practice.stripe_quantity_sync_required = True
        locked_practice.stripe_quantity_sync_target = count
        locked_practice.stripe_quantity_sync_version += 1
        locked_practice.stripe_quantity_sync_error = None
        session.add(locked_practice)
    return count
