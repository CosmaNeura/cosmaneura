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
from app.services.doctor_access import doctor_access_in_force

from activemodel.session_manager import aglobal_session
from app.models.ai_visit_transcript import PydanticBaseModel
from app.models.doctor import Doctor
from app.models.intake.intake import (
    Intake,
    IntakeSchemaQuestion,
    QuestionChoice,
)
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

    doctor = Doctor.get(id=intake.doctor_id)
    if doctor is None or not doctor_access_in_force(doctor):
        # The opaque intake URL is a bearer credential. Return the same
        # response as an unknown token after offboarding so it cannot be used
        # to read or append PHI.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    if intake.is_processing():
        # Only reachable for legacy model-generated intakes, whose questions are
        # written by a background job after creation. Visit Prep seeds its
        # questions at creation time, so it never lands here.
        raise EarlyResponseException(
            data=ChatResponse(
                question=None,
                status="processing",
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
    "the patient's answer. An empty string is a deliberate skip."

    # TODO do we need the question to be posted back? Would probably provide another layer of validation?
    # question: str


class ChatQuestion(PydanticBaseModel):
    """Everything the patient page needs to render one question.

    Deliberately carries no identity: no patient name, no physician name, no
    visit date. This endpoint is unauthenticated and the token in the URL is
    the only credential, so a forwarded or leaked link should expose the
    questions and nothing that ties them to a person. Same rule #135 sets for
    the SMS body -- identity lives in the channel, not the payload.
    """

    question: str
    helper: str | None = None
    kind: Literal["story", "list", "choice"] = "story"
    choices: list[QuestionChoice] | None = None


class ChatProgress(PydanticBaseModel):
    """How far through the visible questions the patient is.

    `total` counts *visible* questions, so it grows by one when a conditional
    follow-up unlocks. Honest, and a one-step change is far less confusing than
    a bar that can never reach the end.
    """

    answered: int
    total: int


class ChatResponse(PydanticBaseModel):
    question: ChatQuestion | None
    status: Literal["processing", "completed", "in_progress"]
    progress: ChatProgress | None = None


def to_chat_question(question: IntakeSchemaQuestion) -> ChatQuestion:
    return ChatQuestion(
        question=question.question,
        helper=question.helper,
        kind=question.kind,
        choices=question.choices,
    )


def progress_for(intake: Intake) -> ChatProgress:
    visible = intake.visible_questions()
    return ChatProgress(
        answered=sum(1 for question in visible if question.is_answered),
        total=len(visible),
    )


@chat_api_app.get("/intake/{intake_id}/question")
def get_last_question(
    request: Request, intake: Intake = Depends(process_intake)
) -> ChatResponse:
    "when returning to the page, return the last question without accepting any user input"
    chat = app.commands.intake.chat.perform(intake, None)
    assert chat

    return ChatResponse(
        question=to_chat_question(chat),
        status="in_progress",
        progress=progress_for(intake),
    )


@chat_api_app.post("/intake/{intake_id}/question")
def get_next_question(
    request: Request, data: ChatInput, intake: Intake = Depends(process_intake)
) -> ChatResponse:
    chat = app.commands.intake.chat.perform(intake, data.answer)

    return ChatResponse(
        question=to_chat_question(chat) if chat else None,
        # "in_progress" rather than the previous "processing": there is a
        # question waiting, which is not the same state as questions still
        # being generated.
        status="completed" if not chat else "in_progress",
        progress=progress_for(intake),
    )
