"""Canonical public-provider capability and active-seat policy.

This module is intentionally the shared in-process boundary for billing and
membership workflows. A browser or downstream service must never derive an
authoritative seat quantity or capability grant independently.
"""

from __future__ import annotations

from typing import Any

from app.models.practice import ClinicianSeatState, PracticeMember
from sqlalchemy import func
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


def active_clinician_seat_count(session: Session, practice_id: Any) -> int:
    """Return Core's authoritative paid-seat quantity for one practice.

    Pending invitations have no membership row and therefore do not count.
    A reversible inactive seat and an irreversible access-revocation tombstone
    both stop occupancy. Callers that mutate membership must serialize on the
    Practice row before using this result to enforce the maximum.
    """

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
