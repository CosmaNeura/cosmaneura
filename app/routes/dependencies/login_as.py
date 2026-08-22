import sentry_sdk
from fastapi import Request
from starlette_context import context

from app import log
from app.routes.admin import SESSION_KEY_LOGIN_AS_USER
from app.services.external_webhook_inbox import provider_log_ref

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
        login_as_doctor = Doctor.get(clerk_id=login_as_clerk_id)

        if not login_as_doctor:
            log.error(
                "login-as user not found",
                event_code="login_as_user_not_found",
                clerk_ref=provider_log_ref(f"clerk-user:{login_as_clerk_id}"),
            )
            return

        doctor_ref = provider_log_ref(f"doctor:{login_as_doctor.id}")
        admin_ref = provider_log_ref(f"doctor:{admin_user.id}")
        log.info(
            "admin login-as activated",
            event_code="login_as_activated",
            doctor_ref=doctor_ref,
            admin_ref=admin_ref,
        )

        request.state.admin_user = admin_user
        request.state.doctor = login_as_doctor

        context["doctor_ref"] = doctor_ref
        context["admin_ref"] = admin_ref

        sentry_sdk.set_extra("admin_ref", admin_ref)
        sentry_sdk.set_user({"id": doctor_ref})
