import sentry_sdk
from fastapi import Request
from starlette_context import context

from app import log
from app.routes.admin import SESSION_KEY_LOGIN_AS_USER

from app.models.doctor import Doctor, DoctorRole


def login_as(request: Request):
    """
    If we are an admin, check if we are logging in as a specific user, and switch to that user.
    """

    # TODO we shouldn't hard code this :/
    if request.url.path.startswith("/internal/v1/admin"):
        return

    if (admin_user := request.state.doctor) and admin_user.role == DoctorRole.admin:
        if SESSION_KEY_LOGIN_AS_USER not in request.session:
            return

        # if we are an admin, let's check if we are logging in as another user
        login_as_clerk_id = request.session[SESSION_KEY_LOGIN_AS_USER]
        login_as_user = Doctor.get(clerk_id=login_as_clerk_id)

        if not login_as_user:
            log.error("user not found", clerk_id=login_as_clerk_id)
            return

        log.info(
            "logging in as user",
            clerk_id=login_as_user.clerk_id,
            doctor_id=login_as_user.id,
            admin_id=admin_user.id,
        )

        request.state.admin_user = admin_user
        request.state.doctor = login_as_user

        context["doctor"] = login_as_user.id
        context["admin_user"] = admin_user.id

        sentry_sdk.set_extra("admin_id", admin_user.id)
        sentry_sdk.set_user({"id": login_as_user.id, "email": login_as_user.email})
