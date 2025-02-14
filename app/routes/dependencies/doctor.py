from datetime import datetime

import sentry_sdk
from fastapi import HTTPException, Request, status
from starlette_context import context

from app.models.doctor import Doctor


def inject_doctor_record(request: Request):
    """
    If a valid Clerk user is detected which is not in the DB, create it. Upstream logic talks to Clerk and makes sure
    the user/doctor is valid.
    """

    clerk_id = request.state.auth_state.payload["sub"]
    doctor = Doctor.find_or_create_by(clerk_id=clerk_id)

    if doctor.deleted_at:
        raise HTTPException(
            status_code=status.HTTP_410_GONE, detail="Your Account has Been Disabled"
        )

    doctor.last_active_at = datetime.now()
    doctor.save()

    request.state.doctor = doctor
    context["doctor"] = doctor

    sentry_sdk.set_user({"id": doctor.id, "email": doctor.email})
