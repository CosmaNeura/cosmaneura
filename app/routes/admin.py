"""
Admin-only routes:

- login_as / user impersonation, similar to https://clerk.com/docs/users/user-impersonation but without a $100/month sub
"""

from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, HTTPException, Path, Request
from pydantic import BaseModel as BasePydanticModel
from pydantic import ConfigDict
from starlette import status
from typeid import TypeID

from app import log
from app.services import usage as usage_service
from app.services.usage import (
    DailyVolumeRow,
    DoctorUsageRow,
    PendingUploadRow,
    RecentAppointmentRow,
    TimeToNoteRow,
)

from app.models.doctor import Doctor, DoctorRole

SESSION_KEY_LOGIN_AS_USER = "login_as_user"


def require_admin(request: Request):
    "Protect routes that require admin access"

    if (admin_user := request.state.doctor) and admin_user.role != DoctorRole.admin:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)


admin_api_app = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)])


class UserSwitchData(BasePydanticModel):
    model_config = ConfigDict(from_attributes=True)

    clerk_id: str
    id: TypeID
    email: str | None


class UserListResponse(BasePydanticModel):
    current_user: UserSwitchData | None
    users: list[UserSwitchData]


@admin_api_app.get("/users")
def user_list(request: Request) -> UserListResponse:
    # remember, these routes are protected from the login_as functionality
    login_as_user = None

    if SESSION_KEY_LOGIN_AS_USER in request.session:
        login_as_clerk_id = request.session[SESSION_KEY_LOGIN_AS_USER]
        login_as_user = Doctor.get(clerk_id=login_as_clerk_id)

    return UserListResponse(
        current_user=login_as_user,  # type: ignore
        users=list(
            Doctor.select(Doctor.clerk_id, Doctor.email, Doctor.id)
            .where(Doctor.role != DoctorRole.admin)
            # Without an explicit order Postgres may return these rows in any
            # order it likes, which reshuffles the login_as picker between
            # requests. created_at alone is not enough — users created inside a
            # single transaction can share a timestamp — so id breaks the tie.
            .order_by(cast(Any, Doctor.created_at).asc(), cast(Any, Doctor.id).asc())
            .all()
        ),  # type: ignore
    )


@admin_api_app.post("/login_as/{user_id}")
def login_as_user(request: Request, user_id: Annotated[str, Path()]):
    if request.state.doctor.clerk_id == user_id:
        log.info("removing login_as")
        request.session[SESSION_KEY_LOGIN_AS_USER] = None
        return

    login_as_user = Doctor.where(
        Doctor.role != DoctorRole.admin, Doctor.clerk_id == user_id
    ).one()

    log.info("login_as_user", login_as_user=login_as_user)

    request.session[SESSION_KEY_LOGIN_AS_USER] = login_as_user.clerk_id  # type: ignore
    return


class UsageResponse(BasePydanticModel):
    recent_appointments: list[RecentAppointmentRow]
    doctor_usage: list[DoctorUsageRow]
    daily_volume: list[DailyVolumeRow]
    pending_uploads: list[PendingUploadRow]
    time_to_note: list[TimeToNoteRow]


@admin_api_app.get("/usage")
def usage_dashboard() -> UsageResponse:
    """Five platform-usage queries the boss runs by hand, lifted to a
    single authenticated endpoint. Admin-only via require_admin on the
    router. Issue #34."""
    return UsageResponse(
        recent_appointments=usage_service.recent_appointments(),
        doctor_usage=usage_service.doctor_usage(),
        daily_volume=usage_service.daily_volume(),
        pending_uploads=usage_service.pending_uploads(),
        time_to_note=usage_service.time_to_note(),
    )
