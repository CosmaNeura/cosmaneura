"""One fail-closed authorization decision for Doctor-facing product access.

The decision is deliberately read-only.  In particular, a legacy pilot grant
does not create or repair a practice membership, change a tenant, or elevate a
Doctor's role.  It is only an additional, revocable basis for bypassing the
personal BAA and practice-entitlement gates.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from app.services.organization_access import member_authorization_basis
from app.services.pilot_access_grandfather import (
    active_pilot_access_grant,
    revoked_pilot_access_grant,
)

from app.models.doctor import Doctor
from app.models.practice import Practice, PracticeMember

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


def resolve_doctor_access(
    doctor: Doctor,
    *,
    require_entitlement: bool = True,
    at: datetime | None = None,
) -> DoctorAccessDecision:
    """Resolve effective product access without mutating identity or tenancy.

    A disabled Doctor, a revoked grandfather grant, or a revoked existing
    membership is an explicit tombstone and therefore outranks every positive
    authorization basis.  An active exact-account pilot grant then permits
    access without requiring a membership, BAA, trial, subscription, or
    billing exemption.  Everyone else continues through the existing
    practice/member authorization rules.
    """

    if doctor.deleted_at is not None:
        return _denied("doctor_disabled")

    if revoked_pilot_access_grant(doctor.clerk_id) is not None:
        return _denied("pilot_grant_revoked")

    member = PracticeMember.get(doctor_id=doctor.id)
    practice = Practice.get(id=member.practice_id) if member is not None else None
    baa_in_force = bool(practice and practice.baa_in_force(at=at))
    entitlement_in_force = bool(practice and practice.entitled(at=at))

    if member is not None and member.access_revoked_at is not None:
        return _denied(
            "practice_membership_revoked",
            baa_in_force=baa_in_force,
            entitlement_in_force=entitlement_in_force,
        )

    if active_pilot_access_grant(doctor, at=at) is not None:
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

    basis = member_authorization_basis(member, at=at)
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
        authorization_basis=basis,
        denial_reason=None,
        baa_in_force=True,
        entitlement_in_force=entitlement_in_force,
    )


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
