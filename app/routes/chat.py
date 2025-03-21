"""
Public, unauthenticated API endpoints for the chat interface.
"""

from time import strftime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Request, status
from typeid import TypeID
from typeid.errors import SuffixValidationException, TypeIDException

import app.commands.intake.chat
import app.commands.intake.create
from app.commands.intake.create import AppointmentResponse

from activemodel.session_manager import aglobal_session
from activemodel.types import TypeIDType
from app.models.ai_visit_transcript import PydanticBaseModel
from app.models.intake.intake import Intake
from sqlalchemy.exc import NoResultFound

chat_api_app = APIRouter(
    prefix="/external/chat",
    dependencies=[
        # NOTE this line could not be more important, look at the underlying implementation!
        Depends(aglobal_session),
    ],
)


def process_intake(intake_id: str = Path(title="Intake ID")) -> Intake:
    try:
        normalized_intake_id = TypeID.from_string(intake_id)
        intake = Intake.one(normalized_intake_id)
    except (TypeIDException, NoResultFound):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
        )

    if intake.appointment.is_expired():
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
        )

    return intake


class ChatResponse(PydanticBaseModel):
    response: str


@chat_api_app.get("/intake/{intake_id}/question")
def get_last_question(
    request: Request, intake_id: Intake = Depends(process_intake)
) -> AppointmentResponse:
    "when returning to the page, return the last question without accepting any user input"
    return app.commands.intake.chat.perform(data, None)


@chat_api_app.post("/intake/{intake_id}/question")
def get_next_question(
    request: Request, data: ChatResponse, intake_id: Intake = Depends(process_intake)
) -> AppointmentResponse:
    return app.commands.intake.chat.perform(data, None)
