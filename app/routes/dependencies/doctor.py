import hmac
from datetime import datetime, timezone
from typing import cast

import sentry_sdk
from fastapi import HTTPException, Request, status
from starlette_context import context

from app import log
from app.services.clerk_identity import verified_primary_email
from app.services.external_webhook_inbox import provider_log_ref
from app.services.pilot_access_grandfather import (
    active_pilot_access_grant,
    revoked_pilot_access_grant,
)

from app.models.doctor import Doctor
from app.models.pilot_access_grandfather_grant import PilotAccessGrandfatherGrant
from app.models.practice import PracticeMember
from sqlalchemy import func

PILOT_IDENTITY_ERROR = "Your account identity could not be verified"
MANAGED_ORGANIZATION_IDENTITY_ERROR = (
    "Your organization account identity could not be verified. "
    "Sign out and sign in again, then contact support if access is still blocked."
)
ACCOUNT_RECOVERY_REQUIRED = (
    "This email is already linked to a previous sign-in. "
    "Contact support to recover the existing account; no records have been lost."
)


def _doctors_for_normalized_email(email: str) -> list[Doctor]:
    """Find every logical owner independent of historical email casing."""

    return cast(
        list[Doctor],
        list(
            Doctor.where(
                func.lower(func.btrim(Doctor.email)) == email.strip().lower()
            ).all()
        ),
    )


def _fetch_clerk_email(clerk_id: str) -> str | None:
    try:
        return verified_primary_email(clerk_id)
    except Exception:
        log.warning(
            "clerk email lookup failed",
            event_code="clerk_email_lookup_failed",
            clerk_ref=provider_log_ref(f"clerk-user:{clerk_id}"),
        )
    return None


def _active_grant_for_clerk(
    clerk_id: str,
    *,
    at: datetime,
) -> PilotAccessGrandfatherGrant | None:
    """Find an active grant without triggering a Clerk lookup for other users."""

    grant = PilotAccessGrandfatherGrant.get(clerk_id=clerk_id, revoked_at=None)
    if grant is None or grant.granted_at > at:
        return None
    return grant


def _deny_unverified_pilot_identity(clerk_id: str) -> None:
    log.warning(
        "pilot access identity verification failed",
        event_code="pilot_access_identity_verification_failed",
        clerk_ref=provider_log_ref(f"clerk-user:{clerk_id}"),
    )
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=PILOT_IDENTITY_ERROR,
    )


def _deny_unverified_managed_identity(clerk_id: str) -> None:
    log.warning(
        "managed organization identity verification failed",
        event_code="managed_organization_identity_verification_failed",
        clerk_ref=provider_log_ref(f"clerk-user:{clerk_id}"),
    )
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=MANAGED_ORGANIZATION_IDENTITY_ERROR,
    )


def _requires_live_managed_identity(doctor: Doctor) -> bool:
    """Whether this Doctor's access can rely on an organization roster."""

    member = PracticeMember.get(doctor_id=doctor.id)
    return bool(member and member.organization_roster_approval_id is not None)


def _require_account_recovery(*, doctor: Doctor, conflicting_owner: Doctor) -> None:
    log.warning(
        "account identity recovery required",
        event_code="account_identity_recovery_required",
        doctor_ref=provider_log_ref(f"doctor:{conflicting_owner.id}"),
        clerk_ref=provider_log_ref(f"clerk-user:{doctor.clerk_id}"),
    )
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=ACCOUNT_RECOVERY_REQUIRED,
    )


def inject_doctor_record(request: Request):
    """
    If a valid Clerk user is detected which is not in the DB, create it. Upstream logic talks to Clerk and makes sure
    the user/doctor is valid.
    """

    clerk_id = request.state.auth_state.payload["sub"]
    # Check the durable offboarding tombstone before upsert. This prevents a
    # revoked pre-login grant from recreating a Doctor merely by signing in.
    if revoked_pilot_access_grant(clerk_id) is not None:
        raise HTTPException(
            status_code=status.HTTP_410_GONE, detail="Your Account has Been Disabled"
        )
    current = datetime.now(timezone.utc)
    grant = _active_grant_for_clerk(clerk_id, at=current)
    doctor = Doctor.upsert(data={"clerk_id": clerk_id}, unique_by="clerk_id")

    if doctor.deleted_at:
        raise HTTPException(
            status_code=status.HTTP_410_GONE, detail="Your Account has Been Disabled"
        )

    # Exact-account pilot access is rooted in both the stable Clerk subject and
    # its reviewed, verified primary email. Revalidate that live identity on
    # every request carrying an active grant; ordinary accounts avoid this
    # remote call.
    managed_identity_required = _requires_live_managed_identity(doctor)
    live_verified_email = (
        _fetch_clerk_email(clerk_id)
        if grant is not None or managed_identity_required
        else None
    )
    normalized_live_email = (
        live_verified_email.strip().lower() if live_verified_email is not None else None
    )
    if grant is not None:
        # Re-read after the remote lookup so a concurrent revocation cannot use
        # the stale pre-request grant row.
        grant = _active_grant_for_clerk(clerk_id, at=current)
        if (
            grant is None
            or normalized_live_email is None
            or not hmac.compare_digest(
                normalized_live_email,
                grant.email_snapshot,
            )
        ):
            _deny_unverified_pilot_identity(clerk_id)

    if managed_identity_required and (
        normalized_live_email is None
        or not doctor.email
        or not hmac.compare_digest(
            doctor.email.strip().lower(),
            normalized_live_email,
        )
    ):
        _deny_unverified_managed_identity(clerk_id)

    email = (
        doctor.email.strip().lower()
        if doctor.email
        else normalized_live_email or _fetch_clerk_email(clerk_id)
    )
    if email:
        email_owners = _doctors_for_normalized_email(email)
        conflicting_owners = [owner for owner in email_owners if owner.id != doctor.id]
        if conflicting_owners:
            _require_account_recovery(
                doctor=doctor,
                conflicting_owner=conflicting_owners[0],
            )
        if not doctor.email:
            doctor.email = email

    if grant is not None and (
        not doctor.email
        or not hmac.compare_digest(
            doctor.email.strip().lower(),
            normalized_live_email or "",
        )
    ):
        _deny_unverified_pilot_identity(clerk_id)

    doctor.last_active_at = datetime.now()
    doctor.save()

    # This validates the persisted Doctor binding and retained email digest as
    # well as rechecking that the grant remains active.
    if grant is not None and active_pilot_access_grant(doctor, at=current) is None:
        _deny_unverified_pilot_identity(clerk_id)

    request.state.doctor = doctor
    doctor_ref = provider_log_ref(f"doctor:{doctor.id}")
    context["doctor_ref"] = doctor_ref

    sentry_sdk.set_user({"id": doctor_ref})
