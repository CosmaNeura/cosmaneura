"""One fail-closed authorization decision for Doctor-facing product access.

The decision is deliberately read-only.  In particular, a legacy pilot grant
does not create or repair a practice membership, change a tenant, or elevate a
Doctor's role.  It is only an additional, revocable basis for bypassing the
personal BAA and practice-entitlement gates.
"""

from __future__ import annotations

import hmac
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from app.services.clerk_identity import verified_primary_email
from app.services.organization_access import member_authorization_basis
from app.services.pilot_access_grandfather import (
    active_pilot_access_grant,
    revoked_pilot_access_grant,
)
from app.services.public_capabilities import CapabilityKey, capability_rollout_allows
from app.services.public_entitlements import (
    CAPABILITY_KEYS,
    public_subscription_in_force,
)
from app.services.public_legal import current_legal_acceptance_flags

from activemodel.session_manager import get_session
from app.models.doctor import Doctor
from app.models.practice import ClinicianSeatState, Practice, PracticeMember

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
    "public_identity_unverified",
    "baa_not_in_force",
    "public_legal_not_in_force",
    "entitlement_not_in_force",
    "credential_pending",
    "credential_needs_review",
    "credential_rejected",
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
    require_credential: bool = True,
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
    if practice is not None and practice.public_onboarding_required:
        entitlement_in_force = public_subscription_in_force(practice, at=at)

    if member is not None and member.access_revoked_at is not None:
        return _denied(
            "practice_membership_revoked",
            baa_in_force=baa_in_force,
            entitlement_in_force=entitlement_in_force,
        )

    if member is not None and member.seat_state != ClinicianSeatState.active.value:
        return _denied(
            "practice_membership_inactive",
            baa_in_force=baa_in_force,
            entitlement_in_force=entitlement_in_force,
        )

    # Grandfather grants preserve the legacy/managed population, but must not
    # bypass the explicit legal and credential boundary of a newly-created
    # public practice.
    if active_pilot_access_grant(doctor, at=at) is not None and not (
        practice is not None and practice.public_onboarding_required
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

    basis = member_authorization_basis(member, at=at)
    if basis not in ("organization_roster", "workforce_attestation"):
        return _denied(
            "practice_access_not_authorized",
            baa_in_force=baa_in_force,
            entitlement_in_force=entitlement_in_force,
        )
    if practice.public_onboarding_required:
        try:
            live_email = verified_primary_email(doctor.clerk_id)
        except Exception:
            live_email = None
        if (
            live_email is None
            or not doctor.email
            or not hmac.compare_digest(
                live_email.strip().lower(),
                doctor.email.strip().lower(),
            )
        ):
            return _denied(
                "public_identity_unverified",
                baa_in_force=baa_in_force,
                entitlement_in_force=entitlement_in_force,
            )
    if not baa_in_force:
        return _denied(
            "baa_not_in_force",
            entitlement_in_force=entitlement_in_force,
        )
    if practice.public_onboarding_required:
        with get_session() as session:
            terms_accepted, privacy_accepted = current_legal_acceptance_flags(
                session,
                practice.id,
            )
        if not (terms_accepted and privacy_accepted):
            return _denied(
                "public_legal_not_in_force",
                baa_in_force=True,
                entitlement_in_force=entitlement_in_force,
            )
    if require_entitlement and not entitlement_in_force:
        return _denied(
            "entitlement_not_in_force",
            baa_in_force=True,
        )
    if (
        practice.public_onboarding_required
        and require_credential
        and member.credential_state != "approved"
    ):
        reason: DoctorAccessDenialReason
        if member.credential_state == "rejected":
            reason = "credential_rejected"
        elif member.credential_state == "needs_review":
            reason = "credential_needs_review"
        else:
            # Missing and forward-incompatible values fail closed as pending.
            reason = "credential_pending"
        return _denied(
            reason,
            baa_in_force=True,
            entitlement_in_force=entitlement_in_force,
        )
    return DoctorAccessDecision(
        allowed=True,
        authorization_basis=basis,
        denial_reason=None,
        baa_in_force=True,
        entitlement_in_force=entitlement_in_force,
    )


def doctor_capability_access_in_force(
    doctor: Doctor,
    capability: CapabilityKey,
    *,
    at: datetime | None = None,
) -> bool:
    """Apply the canonical public rollout after the shared access gates."""

    if not resolve_doctor_access(doctor, at=at).allowed:
        return False
    return doctor_capability_rollout_in_force(doctor, capability)


def doctor_capability_rollout_in_force(
    doctor: Doctor,
    capability: CapabilityKey | str | None,
) -> bool:
    """Apply only the rollout layer after a caller resolved common gates."""

    member = PracticeMember.get(doctor_id=doctor.id)
    practice = Practice.get(id=member.practice_id) if member is not None else None
    if practice is None or not practice.public_onboarding_required:
        return True
    if capability not in CAPABILITY_KEYS:
        return False
    return capability_rollout_allows(
        capability,  # type: ignore[arg-type]
        public_pilot_cohort=practice.public_pilot_cohort,
    )


def doctor_access_in_force(
    doctor: Doctor,
    *,
    require_entitlement: bool = True,
    require_credential: bool = True,
    at: datetime | None = None,
) -> bool:
    """Boolean convenience wrapper around :func:`resolve_doctor_access`."""

    return resolve_doctor_access(
        doctor,
        require_entitlement=require_entitlement,
        require_credential=require_credential,
        at=at,
    ).allowed
