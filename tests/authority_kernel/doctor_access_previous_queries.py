"""Query reference from c5cd21ec; unchanged policy remains in doctor_access.

This reference retains the pre-composition SQL and is used only to compare
synthetic committed states and injected concurrency. No external I/O.
"""

import hmac
from datetime import datetime, timezone
from typing import cast

from app.services import doctor_access as access
from app.services.pilot_access_grandfather import (
    PilotAccessGrandfatherError,
    _aware,
    _email_sha256,
    _normalize_clerk_id,
    _normalize_email,
)

from activemodel.session_manager import get_session
from app.models.doctor import Doctor
from app.models.pilot_access_grandfather_grant import PilotAccessGrandfatherGrant
from app.models.practice import Practice, PracticeMember
from sqlalchemy import or_
from sqlmodel import col, select

DoctorAccessDecision = access.DoctorAccessDecision
doctor_tombstone = access.doctor_tombstone
_denied = access._denied
_decide = access._decide


def revoked_pilot_access_grant(
    clerk_id: str,
) -> PilotAccessGrandfatherGrant | None:
    """Return a durable offboarding tombstone when no active replacement exists."""

    try:
        normalized = _normalize_clerk_id(clerk_id)
    except PilotAccessGrandfatherError:
        return None
    with get_session() as session:
        active = session.exec(
            select(PilotAccessGrandfatherGrant).where(
                PilotAccessGrandfatherGrant.clerk_id == normalized,
                PilotAccessGrandfatherGrant.revoked_at == None,  # noqa: E711
            )
        ).first()
        if active is not None:
            return None
        return cast(
            PilotAccessGrandfatherGrant | None,
            session.exec(
                select(PilotAccessGrandfatherGrant)
                .where(
                    PilotAccessGrandfatherGrant.clerk_id == normalized,
                    PilotAccessGrandfatherGrant.revoked_at != None,  # noqa: E711
                )
                .order_by(col(PilotAccessGrandfatherGrant.revoked_at).desc())
            ).first(),
        )


def active_pilot_access_grant(
    doctor: Doctor,
    *,
    at: datetime | None = None,
) -> PilotAccessGrandfatherGrant | None:
    """Return a candidate record while stable subject and email still match.

    The email check intentionally fails closed after a Clerk/Doctor email
    change. An operator must review the new Clerk export and explicitly issue
    replacement evidence rather than silently transferring it. Callers may use
    the grant as an entitlement for its snapshotted practice; treating it as
    the legacy access basis additionally requires the Doctor's durable
    completed-onboarding marker.
    """

    current = _aware(at)
    if doctor.deleted_at is not None or not doctor.clerk_id or not doctor.email:
        return None
    try:
        clerk_id = _normalize_clerk_id(doctor.clerk_id)
    except PilotAccessGrandfatherError:
        return None

    with get_session() as session:
        persisted_doctor = cast(
            Doctor | None,
            session.exec(
                select(Doctor).where(
                    Doctor.id == doctor.id,
                    Doctor.clerk_id == clerk_id,
                )
            ).one_or_none(),
        )
        if (
            persisted_doctor is None
            or persisted_doctor.deleted_at is not None
            or not persisted_doctor.email
        ):
            return None
        try:
            normalized_email = _normalize_email(persisted_doctor.email)
        except PilotAccessGrandfatherError:
            return None
        email_sha256 = _email_sha256(normalized_email)
        grant = cast(
            PilotAccessGrandfatherGrant | None,
            session.exec(
                select(PilotAccessGrandfatherGrant).where(
                    PilotAccessGrandfatherGrant.clerk_id == clerk_id,
                    PilotAccessGrandfatherGrant.granted_at <= current,
                    or_(
                        PilotAccessGrandfatherGrant.revoked_at == None,  # noqa: E711
                        PilotAccessGrandfatherGrant.revoked_at > current,
                    ),
                )
            ).one_or_none(),
        )
        if grant is None:
            return None
        if (
            grant.doctor_id_snapshot is not None
            and grant.doctor_id_snapshot != doctor.id
        ):
            return None
        if grant.email_snapshot != normalized_email:
            return None
        if not hmac.compare_digest(grant.email_sha256, email_sha256):
            return None
        return grant


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

    tombstone = doctor_tombstone(doctor)
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
