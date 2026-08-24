"""
Public, unauthenticated API endpoints for the chat interface.
"""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Request, status
from fastapi.responses import JSONResponse
from typeid import TypeID
from typeid.errors import TypeIDException

import app.commands.intake.chat
import app.commands.intake.create
from app.routes.dependencies.view_as import deny_view_as_browser_activity
from app.routes.errors import EarlyResponseException
from app.services.doctor_access import doctor_access_in_force

from activemodel.session_manager import aglobal_session
from app.models.ai_visit_transcript import PydanticBaseModel
from app.models.doctor import Doctor
from app.models.intake.intake import Intake
from sqlalchemy.exc import NoResultFound

chat_api_app = APIRouter(
    prefix="/external/chat",
    dependencies=[
        # NOTE this line could not be more important, look at the underlying implementation!
        Depends(aglobal_session),
        # Opaque patient intake links are interactive and can append PHI.
        # They must not be usable from an active support view-as browser.
        Depends(deny_view_as_browser_activity),
    ],
)


def process_intake(intake_id: str = Path(title="Intake ID")) -> Intake | JSONResponse:
    try:
        normalized_intake_id = TypeID.from_string(intake_id)
        intake = Intake.one(normalized_intake_id)
    except (TypeIDException, NoResultFound):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
        )

    doctor = Doctor.get(id=intake.doctor_id)
    if doctor is None or not doctor_access_in_force(doctor):
        # The opaque intake URL is a bearer credential. Return the same
        # response as an unknown token after offboarding so it cannot be used
        # to read or append PHI.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    if intake.is_processing():
        raise EarlyResponseException(
            data=ChatResponse(
                question=None,
                status="in_progress",
            ),
        )

    if intake.appointment.is_expired() or intake.is_complete():
        raise EarlyResponseException(
            data=ChatResponse(
                question=None,
                status="completed",
            ),
        )

    return intake


class ChatInput(PydanticBaseModel):
    answer: str
    # TODO do we need the question to be posted back? Would probably provide another layer of validation?
    # question: str


class ChatResponse(PydanticBaseModel):
    question: str | None
    status: Literal["processing", "completed", "in_progress"]


@chat_api_app.get("/intake/{intake_id}/question")
def get_last_question(
    request: Request, intake: Intake = Depends(process_intake)
) -> ChatResponse:
    "when returning to the page, return the last question without accepting any user input"
    chat = app.commands.intake.chat.perform(intake, None)
    assert chat

    return ChatResponse(question=chat.question, status="in_progress")


@chat_api_app.post("/intake/{intake_id}/question")
def get_next_question(
    request: Request, data: ChatInput, intake: Intake = Depends(process_intake)
) -> ChatResponse:
    chat = app.commands.intake.chat.perform(intake, data.answer)

    return ChatResponse(
        question=chat and chat.question,
        status="completed" if not chat else "processing",
    )
