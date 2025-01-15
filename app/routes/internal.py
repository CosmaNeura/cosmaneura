from typing import Annotated

from fastapi import APIRouter, Depends, Path, Request
from pydantic import BaseModel, computed_field

import app.commands.uploads.sas
import app.commands.uploads.upload_visit_audio_file

from activemodel.session_manager import global_session, global_session_2
from activemodel.types import TypeIDType
from app.models.ai_visit_note import AIVisitNote
from app.models.ai_visit_transcript import AIVisitTranscript
from app.models.appointment import Appointment
from app.models.patient import Patient

from ..configuration.clerk import CLERK_PRIVATE_KEY
from .dependencies.clerk import AuthenticateClerkRequest
from .dependencies.doctor import inject_doctor_record

# extract into variable for test import to easily override dependencies
authenticate_clerk_request_middleware = AuthenticateClerkRequest(CLERK_PRIVATE_KEY)

internal_api_app = APIRouter(
    prefix="/internal/v1",
    # TODO unclear what the tags are used for...
    tags=["private"],
    # think of dependencies as middleware
    dependencies=[
        # Depends(global_session),
        Depends(global_session_2),
        Depends(authenticate_clerk_request_middleware),
        Depends(inject_doctor_record),
    ],
)


class IntakeList(BaseModel, extra="forbid"):
    appointments: list[Appointment]


class NotetakingData(BaseModel, extra="forbid"):
    appointments: list[Appointment]


class SettingsData(BaseModel, extra="forbid"):
    settings: dict


@internal_api_app.get("/intake")
def intake_list() -> IntakeList:
    # TODO filter appointment data
    return IntakeList(appointments=list(Appointment.all()))


@internal_api_app.get("/notes")
def note_list(request: Request) -> NotetakingData:
    """
    A separate endpoint is used to pull data for an individual note, so we just need the appointment ID
    at this point.
    """

    # visit_notes = AIVisitNote.select(AIVisitNote.doctor_id == request.state.doctor.id)
    # visit_notes = AIVisitNote.all()

    return NotetakingData(appointments=list(Appointment.all()))


from functools import wraps

from activemodel.session_manager import get_session
from sqlalchemy.orm.exc import DetachedInstanceError


def safe_computed_field(func):
    @computed_field
    @property
    @wraps(func)
    def wrapper(self, *args, **kwargs):
        try:
            return func(self, *args, **kwargs)
        except DetachedInstanceError:
            breakpoint()
            with get_session() as session:
                merged_instance = session.merge(self)
                return func(merged_instance, *args, **kwargs)

    return wrapper


class NoteDetail(BaseModel, extra="forbid"):
    appointment: Appointment
    transcript: AIVisitTranscript | None
    notes: list[AIVisitNote] | None

    @computed_field
    @property
    # @safe_computed_field
    def patient(self) -> Patient:
        return self.appointment.patient


@internal_api_app.get("/notes/{appointment_id}")
def note_detail(
    request: Request,
    appointment_id: Annotated[TypeIDType, Path()],
) -> NoteDetail:
    # TODO error handling for bad IDs
    note_detail = NoteDetail(
        appointment=Appointment.get(id=appointment_id),
        transcript=AIVisitTranscript.get(appointment_id=appointment_id),
        # TODO should be able to handle a generator, right?
        notes=list(
            AIVisitNote.select()
            .where(AIVisitNote.appointment_id == appointment_id)
            .all()
        ),
    )

    note_detail.patient

    return note_detail


@internal_api_app.post("/visit-audio-file/get-signed-url", response_model=str)
def generate_signed_upload_url():
    return app.commands.uploads.sas.perform()


@internal_api_app.post("/visit-audio-file/upload-complete")
def upload_visit_audio_file(request: Request, file_name: str) -> None:
    return app.commands.uploads.upload_visit_audio_file.perform(
        user=request.state.user, blob_name=file_name
    )


@internal_api_app.get("/settings")
def settings_data() -> SettingsData:
    return SettingsData(settings={})
