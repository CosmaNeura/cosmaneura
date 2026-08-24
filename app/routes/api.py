"""
Logic for an API server. This is distinct from the "app" API server, which hosts static assets
and is authenticated via a clerk token, and is meant to be consumed by an external user.
"""

import sentry_sdk
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from starlette_context import context
from typeid import TypeID
from typeid.errors import TypeIDException

import app.commands.intake.chat
import app.commands.intake.create
from app.commands.intake.create import AppointmentRequest, AppointmentResponse
from app.routes.dependencies.view_as import deny_view_as_browser_activity
from app.services.doctor_access import doctor_access_in_force
from app.services.external_webhook_inbox import provider_log_ref

from app.models.doctor import API_KEY_PREFIX, Doctor

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")

UNAUTHORIZED_EXCEPTION = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Invalid key",
)


def authenticate_api_request_middleware(
    request: Request, token: str = Depends(oauth2_scheme)
):
    "parse bearer token, lookup associated user, and inject into request state"

    try:
        token_as_typeid = TypeID.from_string(token)
    except TypeIDException:
        raise UNAUTHORIZED_EXCEPTION

    if token_as_typeid.prefix != API_KEY_PREFIX:
        raise UNAUTHORIZED_EXCEPTION

    api_user = Doctor.get(api_key=token_as_typeid.uuid)

    if not api_user:
        raise UNAUTHORIZED_EXCEPTION

    if api_user.deleted_at:
        raise HTTPException(
            status_code=status.HTTP_410_GONE, detail="Your API access has been disabled"
        )

    if not doctor_access_in_force(api_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="API access is not active",
        )

    request.state.api_user = api_user

    api_user_ref = provider_log_ref(f"doctor:{api_user.id}")
    context["api_user_ref"] = api_user_ref

    sentry_sdk.set_user({"id": api_user_ref})
    sentry_sdk.set_extra("api_user", True)


external_api_app = APIRouter(
    prefix="/external/v1", dependencies=[Depends(authenticate_api_request_middleware)]
)


@external_api_app.get("/ping")
def external_api_ping():
    "basic uptime + API authentication check"
    return {"status": "ok"}


@external_api_app.post("/intake")
def create_intake(request: Request, data: AppointmentRequest) -> AppointmentResponse:
    deny_view_as_browser_activity(request)
    api_user: Doctor = request.state.api_user
    requested_email = str(data.doctor_email or "").strip().lower()
    authenticated_email = (api_user.email or "").strip().lower()
    if not requested_email or requested_email != authenticated_email:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="The API key may create intake records only for its own account",
        )
    # The API key, not a caller-controlled lookup, is the authoritative target.
    data.doctor = api_user
    return app.commands.intake.create.perform(data)
