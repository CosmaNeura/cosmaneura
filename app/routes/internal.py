from fastapi import APIRouter, Depends
from pydantic import BaseModel

import app.commands.uploads.sas

from ..configuration.clerk import CLERK_PRIVATE_KEY
from .dependencies.clerk import AuthenticateRequest
from .dependencies.user import inject_user_record

internal_api_app = APIRouter(
    prefix="/internal/v1",
    # TODO unclear what the tags are used for...
    tags=["private"],
    dependencies=[
        Depends(AuthenticateRequest(CLERK_PRIVATE_KEY)),
        Depends(inject_user_record),
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
def visit_audio_file_get_signed_url():
    return app.commands.uploads.sas.perform()


@internal_api_app.post("/visit-audio-file/upload-complete")
def visit_audio_file_upload(blob_name: str) -> None:
    return None


@internal_api_app.get("/settings")
def settings_data() -> SettingsData:
    return SettingsData(settings={})
