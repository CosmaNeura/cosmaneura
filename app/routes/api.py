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

import app.commands.intake.create
from app.commands.intake.create import AppointmentRequest, AppointmentResponse

from app.models.doctor import API_KEY_PREFIX, Doctor

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")

UNAUTHORIZED_EXCEPTION = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Invalid key",
)


def authenticate_api_request_middleware(
    request: Request, token: str = Depends(oauth2_scheme)
):
    try:
        token_as_typeid = TypeID.from_string(token)
    except TypeIDException:
        raise UNAUTHORIZED_EXCEPTION

    if token_as_typeid.prefix != API_KEY_PREFIX:
        raise UNAUTHORIZED_EXCEPTION

    api_doctor = Doctor.get(api_key=token_as_typeid.uuid)

    if not api_doctor:
        raise UNAUTHORIZED_EXCEPTION

    if api_doctor.deleted_at:
        raise HTTPException(
            status_code=status.HTTP_410_GONE, detail="Your Account has Been Disabled"
        )

    request.state.api_doctor = api_doctor

    context["api_doctor"] = api_doctor

    sentry_sdk.set_user({"id": api_doctor.id, "email": api_doctor.email})
    sentry_sdk.set_extra("api_doctor", True)


external_api_app = APIRouter(
    prefix="/external/v1", dependencies=[Depends(authenticate_api_request_middleware)]
)


@external_api_app.get("/ping")
def external_api_ping():
    "basic uptime + API authentication check"
    return {"status": "ok"}


@external_api_app.post("/intake")
def create_intake(request: Request, data: AppointmentRequest) -> AppointmentResponse:
    return app.commands.intake.create.perform(data, request.state.api_doctor)
