"""One fail-closed authorization decision for Doctor-facing product access.

The decision is deliberately read-only. An exact, active legacy-pilot grant on
an already-onboarded Doctor is an independent authorization basis: it preserves
the reviewed cohort's access without creating or rewriting membership, BAA, or
billing state. Disabled Doctors and explicit grant or membership revocations
remain hard denials.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from app.services.organization_access import member_authorization_basis
from app.services.pilot_access_grandfather import (
    active_pilot_access_grant,
    revoked_pilot_access_grant,
)

from app.models.doctor import Doctor
from app.models.practice import (
    MemberAccessState,
    Practice,
    PracticeMember,
    member_access_state,
)

DoctorAccessBasis = Literal[
    "pilot_grant",
    "organization_roster",
    "workforce_attestation",
]
DoctorAccessDenialReason = Literal[
    "doctor_disabled",
    "pilot_grant_revoked",
    "practice_membership_required",
    "practice_membership_revoked",
    "practice_membership_inactive",
    "practice_not_found",
    "practice_access_not_authorized",
    "baa_not_in_force",
    "entitlement_not_in_force",
]


@dataclass(frozen=True)
class DoctorAccessDecision:
    """Effective access plus the underlying BAA and entitlement facts."""

    allowed: bool
    authorization_basis: DoctorAccessBasis | None
    denial_reason: DoctorAccessDenialReason | None
    baa_in_force: bool
    entitlement_in_force: bool


def _denied(
    reason: DoctorAccessDenialReason,
    *,
    baa_in_force: bool = False,
    entitlement_in_force: bool = False,
) -> DoctorAccessDecision:
    return DoctorAccessDecision(
        allowed=False,
        authorization_basis=None,
        denial_reason=reason,
        baa_in_force=baa_in_force,
        entitlement_in_force=entitlement_in_force,
    )


def _has_valid_legacy_pilot_access(
    doctor: Doctor,
    *,
    has_active_grant: bool,
) -> bool:
    """Recognize an active pilot grant for a completed-onboarding Doctor.

    The current fixed cohort's grants all belong to Doctors whose onboarding
    was already complete before the access regression. Requiring that durable
    marker excludes pre-login grant rows without adding an aggregate cohort
    scan to every authorized request.
    """

    return doctor.onboarding_completed_at is not None and has_active_grant


def _doctor_tombstone(doctor: Doctor) -> DoctorAccessDenialReason | None:
    if doctor.deleted_at is not None:
        return "doctor_disabled"
    if revoked_pilot_access_grant(doctor.clerk_id) is not None:
        return "pilot_grant_revoked"
    return None


def _membership_tombstone(
    member: PracticeMember, at: datetime
) -> DoctorAccessDenialReason | None:
    state = member_access_state(member, at=at)
    if state is MemberAccessState.permanently_revoked:
        return "practice_membership_revoked"
    if state is MemberAccessState.revoked:
        return "practice_membership_inactive"
    return None


def _decide(
    doctor: Doctor,
    member: PracticeMember | None,
    practice: Practice | None,
    *,
    require_entitlement: bool,
    at: datetime,
) -> DoctorAccessDecision:
    """The access rules for a Doctor whose own tombstones are already cleared."""

    baa_in_force = bool(practice and practice.baa_in_force(at=at))
    pilot_grant = active_pilot_access_grant(doctor, at=at)
    pilot_entitlement_in_force = bool(
        practice and pilot_grant and pilot_grant.practice_id_snapshot == practice.id
    )
    entitlement_in_force = bool(
        practice and (practice.entitled(at=at) or pilot_entitlement_in_force)
    )

    tombstone = _membership_tombstone(member, at) if member is not None else None
    if tombstone is not None:
        return _denied(
            tombstone,
            baa_in_force=baa_in_force,
            entitlement_in_force=entitlement_in_force,
        )

    if _has_valid_legacy_pilot_access(
        doctor,
        has_active_grant=pilot_grant is not None,
    ):
        return DoctorAccessDecision(
            allowed=True,
            authorization_basis="pilot_grant",
            denial_reason=None,
            baa_in_force=baa_in_force,
            entitlement_in_force=entitlement_in_force,
        )

    if member is None:
        return _denied("practice_membership_required")
    if practice is None:
        return _denied("practice_not_found")

    basis = member_authorization_basis(member, at=at, practice=practice)
    if basis not in ("organization_roster", "workforce_attestation"):
        return _denied(
            "practice_access_not_authorized",
            baa_in_force=baa_in_force,
            entitlement_in_force=entitlement_in_force,
        )
    if not baa_in_force:
        return _denied(
            "baa_not_in_force",
            entitlement_in_force=entitlement_in_force,
        )
    if require_entitlement and not entitlement_in_force:
        return _denied(
            "entitlement_not_in_force",
            baa_in_force=True,
        )
    return DoctorAccessDecision(
        allowed=True,
        authorization_basis="pilot_grant" if pilot_entitlement_in_force else basis,
        denial_reason=None,
        baa_in_force=True,
        entitlement_in_force=entitlement_in_force,
    )


def resolve_doctor_access(
    doctor: Doctor,
    *,
    require_entitlement: bool = True,
    at: datetime | None = None,
) -> DoctorAccessDecision:
    """Resolve effective product access without mutating identity or tenancy.

    A disabled Doctor, a revoked grandfather grant, or a revoked or inactive
    membership is an explicit tombstone and therefore outranks every positive
    authorization basis. An active exact-account pilot grant on an
    already-onboarded Doctor then permits access without requiring a membership,
    BAA, trial, subscription, or billing exemption. Everyone else continues
    through the normal practice/member authorization rules.
    """

    tombstone = _doctor_tombstone(doctor)
    if tombstone is not None:
        return _denied(tombstone)

    member = PracticeMember.get(doctor_id=doctor.id)
    practice = Practice.get(id=member.practice_id) if member is not None else None
    return _decide(
        doctor,
        member,
        practice,
        require_entitlement=require_entitlement,
        at=at or datetime.now(timezone.utc),
    )


def practice_members_access_in_force(
    practice: Practice,
    members: Iterable[tuple[PracticeMember, Doctor]],
    *,
    at: datetime | None = None,
) -> list[bool]:
    """:func:`doctor_access_in_force` for each of one practice's members.

    Equivalent per member, but loads the practice once and settles revoked or
    unseated memberships before any per-Doctor lookup, so the cost of a roster
    does not grow with its retained revocation history.
    """

    current = at or datetime.now(timezone.utc)
    results: list[bool] = []
    for member, doctor in members:
        if member.practice_id != practice.id or member.doctor_id != doctor.id:
            raise ValueError("member does not belong to this practice and doctor")
        if (
            _membership_tombstone(member, current) is not None
            or _doctor_tombstone(doctor) is not None
        ):
            results.append(False)
            continue
        results.append(
            _decide(
                doctor,
                member,
                practice,
                require_entitlement=True,
                at=current,
            ).allowed
        )
    return results


def doctor_access_in_force(
    doctor: Doctor,
    *,
    require_entitlement: bool = True,
    at: datetime | None = None,
) -> bool:
    """Boolean convenience wrapper around :func:`resolve_doctor_access`."""

    return resolve_doctor_access(
        doctor,
        require_entitlement=require_entitlement,
        at=at,
    ).allowed
