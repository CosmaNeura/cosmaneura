from fastapi import HTTPException, Request, status

from app.models.doctor import Doctor


def inject_doctor_record(request: Request):
    clerk_id = request.state.auth_state.payload["sub"]
    user = Doctor.find_or_create_by(clerk_id=clerk_id)

    if user.deleted_at:
        raise HTTPException(
            status_code=status.HTTP_410_GONE, detail="Your Account has Been Disabled"
        )

    request.state.user = user
