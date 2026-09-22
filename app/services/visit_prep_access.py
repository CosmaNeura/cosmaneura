"""Read-only Visit Prep eligibility and release-control decisions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from app.configuration.visit_prep import VISIT_PREP_SETTINGS, VisitPrepSettings
from app.services.doctor_access import resolve_doctor_access
from app.services.organization_access import member_authorization_basis

from activemodel.types import TypeIDType
from app.models.doctor import Doctor
from app.models.practice import Practice, PracticeMember

VisitPrepAccessDenialReason = Literal[
    "configuration_invalid",
    "feature_disabled",
    "doctor_disabled",
    "pilot_grant_revoked",
    "practice_membership_required",
    "practice_membership_revoked",
    "practice_membership_inactive",
    "practice_not_found",
    "practice_access_not_authorized",
    "baa_not_in_force",
    "entitlement_not_in_force",
    "cohort_not_enabled",
]


@dataclass(frozen=True)
class VisitPrepAccessDecision:
    """Capabilities for one authenticated physician context.

    IDs remain available to server-side callers for ownership checks in later
    Visit Prep layers. They must not be serialized into the settings response.
    """

    eligible: bool
    can_manage: bool
    can_send_email: bool
    can_send_sms: bool
    doctor_id: TypeIDType
    practice_id: TypeIDType | None
    practice_member_id: TypeIDType | None
    denial_reason: VisitPrepAccessDenialReason | None
    view_as_active: bool


def _denied(
    doctor: Doctor,
    reason: VisitPrepAccessDenialReason,
    *,
    member: PracticeMember | None = None,
    practice: Practice | None = None,
    view_as_active: bool = False,
) -> VisitPrepAccessDecision:
    return VisitPrepAccessDecision(
        eligible=False,
        can_manage=False,
        can_send_email=False,
        can_send_sms=False,
        doctor_id=doctor.id,
        practice_id=practice.id if practice is not None else None,
        practice_member_id=member.id if member is not None else None,
        denial_reason=reason,
        view_as_active=view_as_active,
    )


def resolve_visit_prep_access(
    doctor: Doctor,
    *,
    view_as_active: bool = False,
    settings: VisitPrepSettings = VISIT_PREP_SETTINGS,
    at: datetime | None = None,
) -> VisitPrepAccessDecision:
    """Resolve Visit Prep capabilities without mutating identity or tenancy.

    The rollout cohort is necessary but never sufficient. A physician must
    also have a live, authorized PracticeMember row, an in-force practice BAA,
    and a current entitlement. In particular, the legacy exact-account pilot
    grant cannot unlock Visit Prep on its own.
    """

    if not settings.configuration_valid:
        return _denied(
            doctor,
            "configuration_invalid",
            view_as_active=view_as_active,
        )
    if not settings.enabled:
        return _denied(doctor, "feature_disabled", view_as_active=view_as_active)

    # Preserve every account-level denial from the shared access contract while
    # independently enforcing the stronger Visit Prep practice requirements.
    account_access = resolve_doctor_access(doctor, at=at)
    if not account_access.allowed:
        assert account_access.denial_reason is not None
        return _denied(
            doctor,
            account_access.denial_reason,
            view_as_active=view_as_active,
        )

    member = PracticeMember.get(doctor_id=doctor.id)
    if member is None:
        return _denied(
            doctor,
            "practice_membership_required",
            view_as_active=view_as_active,
        )
    if member.access_revoked_at is not None:
        return _denied(
            doctor,
            "practice_membership_revoked",
            member=member,
            view_as_active=view_as_active,
        )

    practice = Practice.get(id=member.practice_id)
    if practice is None:
        return _denied(
            doctor,
            "practice_not_found",
            member=member,
            view_as_active=view_as_active,
        )
    if member_authorization_basis(member, at=at) not in {
        "organization_roster",
        "workforce_attestation",
    }:
        return _denied(
            doctor,
            "practice_access_not_authorized",
            member=member,
            practice=practice,
            view_as_active=view_as_active,
        )
    if not practice.baa_in_force(at=at):
        return _denied(
            doctor,
            "baa_not_in_force",
            member=member,
            practice=practice,
            view_as_active=view_as_active,
        )
    if not practice.entitled(at=at):
        return _denied(
            doctor,
            "entitlement_not_in_force",
            member=member,
            practice=practice,
            view_as_active=view_as_active,
        )

    doctor_enabled = str(doctor.id) in settings.enabled_doctor_ids
    practice_enabled = str(practice.id) in settings.enabled_practice_ids
    if not doctor_enabled and not practice_enabled:
        return _denied(
            doctor,
            "cohort_not_enabled",
            member=member,
            practice=practice,
            view_as_active=view_as_active,
        )

    can_manage = not view_as_active
    return VisitPrepAccessDecision(
        eligible=True,
        can_manage=can_manage,
        can_send_email=can_manage and settings.email_send_enabled,
        can_send_sms=can_manage and settings.sms_send_enabled,
        doctor_id=doctor.id,
        practice_id=practice.id,
        practice_member_id=member.id,
        denial_reason=None,
        view_as_active=view_as_active,
    )
