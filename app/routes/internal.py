from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

import app.commands.uploads.sas
import app.commands.uploads.upload_visit_audio_file

from app.models.ai_visit_note import AIVisitNote

from ..configuration.clerk import CLERK_PRIVATE_KEY
from .dependencies.clerk import AuthenticateRequest
from .dependencies.doctor import inject_doctor_record

internal_api_app = APIRouter(
    prefix="/internal/v1",
    # TODO unclear what the tags are used for...
    tags=["private"],
    # think of dependencies as middleware
    dependencies=[
        Depends(AuthenticateRequest(CLERK_PRIVATE_KEY)),
        Depends(inject_doctor_record),
    ],
)


class IntakeData(BaseModel, extra="forbid"):
    appointments: list[dict]


class NotetakingData(BaseModel, extra="forbid"):
    notes: list[dict]


class SettingsData(BaseModel, extra="forbid"):
    settings: dict


@internal_api_app.get("/intake")
def intake_data() -> IntakeData:
    return IntakeData(appointments=[])


@internal_api_app.get("/notes")
def note_data() -> NotetakingData:
    return NotetakingData(notes=[])


@internal_api_app.post("/visit-audio-file/get-signed-url", response_model=str)
def generate_signed_upload_url():
    return app.commands.uploads.sas.perform()


@internal_api_app.post("/visit-audio-file/upload-complete")
def upload_visit_audio_file(request: Request, file_name: str) -> None:
    return app.commands.uploads.upload_visit_audio_file.perform(
        request.state.user, file_name
    )


@internal_api_app.get("/settings")
def settings_data() -> SettingsData:
    return SettingsData(settings={})
