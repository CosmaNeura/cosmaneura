from datetime import datetime

import sentry_sdk
from fastapi import HTTPException, Request, status
from starlette_context import context

from app import log
from app.services.clerk_identity import verified_primary_email

from app.models.doctor import Doctor


def _fetch_clerk_email(clerk_id: str) -> str | None:
    try:
        return verified_primary_email(clerk_id)
    except Exception:
        log.warning(
            "failed to fetch email from clerk", clerk_id=clerk_id, exc_info=True
        )
    return None


def inject_doctor_record(request: Request):
    """
    If a valid Clerk user is detected which is not in the DB, create it. Upstream logic talks to Clerk and makes sure
    the user/doctor is valid.
    """

    clerk_id = request.state.auth_state.payload["sub"]
    doctor = Doctor.upsert(data={"clerk_id": clerk_id}, unique_by="clerk_id")

    if not doctor.email:
        email = _fetch_clerk_email(clerk_id)
        if email and not Doctor.get(email=email):
            doctor.email = email

    if doctor.deleted_at:
        raise HTTPException(
            status_code=status.HTTP_410_GONE, detail="Your Account has Been Disabled"
        )

    doctor.last_active_at = datetime.now()
    doctor.save()

    request.state.doctor = doctor
    context["doctor"] = doctor

    sentry_sdk.set_user({"id": doctor.id, "email": doctor.email})
