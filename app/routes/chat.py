"""
Public, unauthenticated API endpoints for the chat interface.
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

from app.models.doctor import API_KEY_PREFIX, Doctor

chat_api_app = APIRouter(
    prefix="/chat",
    # dependencies=[Depends(authenticate_api_request_middleware)]
)


@chat_api_app.get("/question")
def get_last_question(
    request: Request, data: AppointmentRequest
) -> AppointmentResponse:
    "when returning to the page, return the last question without accepting any user input"
    return app.commands.intake.chat.perform(data, None)


@chat_api_app.post("/question")
def get_next_question(
    request: Request, data: AppointmentRequest
) -> AppointmentResponse:
    return app.commands.intake.chat.perform(data, None)
