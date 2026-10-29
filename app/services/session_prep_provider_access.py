"""One strict, current access decision for provider Session Prep.

The platform's legacy pilot grant intentionally preserves broad product access,
but it is not sufficient authority for Session Prep.  This feature can expose
longitudinal chart context, so every discovery and execution boundary requires
completed onboarding plus a live practice membership, BAA, and platform
entitlement.  The global rollout switch is deliberately kept outside this
predicate so turning it on can never manufacture account authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, cast

from app.services.organization_access import member_authorization_basis

from activemodel.session_manager import get_session
from app.models.doctor import Doctor
from app.models.pilot_access_grandfather_grant import PilotAccessGrandfatherGrant
from app.models.practice import (
    SEAT_HOLDING_ACCESS_STATES,
    Practice,
    PracticeMember,
    member_access_state,
)
from sqlmodel import Session, col, select

SessionPrepProviderAccessDenial = Literal[
    "provider_unavailable",
    "pilot_grant_revoked",
    "onboarding_incomplete",
    "practice_membership_required",
    "practice_membership_inactive",
    "practice_access_not_authorized",
    "baa_not_in_force",
    "entitlement_not_in_force",
]


@dataclass(frozen=True)
class SessionPrepProviderAccessDecision:
    allowed: bool
    denial_reason: SessionPrepProviderAccessDenial | None
    practice: Practice | None = None


def _denied(
    reason: SessionPrepProviderAccessDenial,
) -> SessionPrepProviderAccessDecision:
    return SessionPrepProviderAccessDecision(allowed=False, denial_reason=reason)


def resolve_session_prep_provider_access(
    doctor: Doctor,
    *,
    session: Session | None = None,
    at: datetime | None = None,
) -> SessionPrepProviderAccessDecision:
    """Resolve current account authority without accepting pilot-only access."""

    current_at = at or datetime.now(UTC)
    if session is None:
        with get_session() as managed:
            return resolve_session_prep_provider_access(
                doctor,
                session=cast(Session, managed),
                at=current_at,
            )

    active_pilot_grant = (
        select(col(PilotAccessGrandfatherGrant.id))
        .where(
            PilotAccessGrandfatherGrant.clerk_id == doctor.clerk_id,
            PilotAccessGrandfatherGrant.revoked_at == None,  # noqa: E711
        )
        .correlate(None)
        .exists()
    )
    revoked_pilot_grant = (
        select(col(PilotAccessGrandfatherGrant.id))
        .where(
            PilotAccessGrandfatherGrant.clerk_id == doctor.clerk_id,
            PilotAccessGrandfatherGrant.revoked_at != None,  # noqa: E711
            ~active_pilot_grant,
        )
        .order_by(col(PilotAccessGrandfatherGrant.revoked_at).desc())
        .limit(1)
        .correlate(None)
        .scalar_subquery()
    )
    rows = session.exec(
        select(Doctor, PracticeMember, Practice, PilotAccessGrandfatherGrant)
        .select_from(Doctor)
        .outerjoin(PracticeMember, col(PracticeMember.doctor_id) == col(Doctor.id))
        .outerjoin(Practice, col(Practice.id) == col(PracticeMember.practice_id))
        .outerjoin(
            PilotAccessGrandfatherGrant,
            col(PilotAccessGrandfatherGrant.id) == revoked_pilot_grant,
        )
        .where(Doctor.id == doctor.id, Doctor.clerk_id == doctor.clerk_id)
        .execution_options(populate_existing=True)
    ).one_or_none()
    if rows is None:
        return _denied("provider_unavailable")
    current_doctor, member, practice, revoked_pilot = rows
    if current_doctor.deleted_at is not None:
        return _denied("provider_unavailable")
    if revoked_pilot is not None:
        return _denied("pilot_grant_revoked")
    if current_doctor.onboarding_completed_at is None:
        return _denied("onboarding_incomplete")
    if member is None or practice is None:
        return _denied("practice_membership_required")
    if member_access_state(member, at=current_at) not in SEAT_HOLDING_ACCESS_STATES:
        return _denied("practice_membership_inactive")
    if member_authorization_basis(member, at=current_at, practice=practice) not in (
        "organization_roster",
        "workforce_attestation",
    ):
        return _denied("practice_access_not_authorized")
    if not practice.baa_in_force(at=current_at):
        return _denied("baa_not_in_force")
    # A legacy pilot grant can count as broad product entitlement. Session Prep
    # deliberately requires the practice's ordinary, current entitlement.
    if not practice.entitled(at=current_at):
        return _denied("entitlement_not_in_force")
    return SessionPrepProviderAccessDecision(
        allowed=True,
        denial_reason=None,
        practice=practice,
    )
