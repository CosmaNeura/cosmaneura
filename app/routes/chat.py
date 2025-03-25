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
from app.routes.errors import EarlyResponseException

from activemodel.session_manager import aglobal_session
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


def process_intake(intake_id: str = Path(title="Intake ID")) -> Intake | JSONResponse:
    try:
        normalized_intake_id = TypeID.from_string(intake_id)
        intake = Intake.one(normalized_intake_id)
    except (TypeIDException, NoResultFound):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
        )

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


class ChatResponse(PydanticBaseModel):
    question: str | None
    status: Literal["processing", "completed", "in_progress"]


@chat_api_app.get("/intake/{intake_id}/question")
def get_last_question(
    request: Request, intake: Intake = Depends(process_intake)
) -> ChatResponse:
    "when returning to the page, return the last question without accepting any user input"
    next_question = app.commands.intake.chat.perform(intake, None)
    assert next_question

    return ChatResponse(question=next_question.question, status="in_progress")


@chat_api_app.post("/intake/{intake_id}/question")
def get_next_question(
    request: Request, data: ChatResponse, intake: Intake = Depends(process_intake)
) -> ChatResponse:
    next_question = app.commands.intake.chat.perform(intake, None)

    return ChatResponse(
        question=next_question and next_question.question,
        status="completed" if not next_question else "processing",
    )
