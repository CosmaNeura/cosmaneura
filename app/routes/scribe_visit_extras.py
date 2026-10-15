"""Dashboard bridge to the same current-author Scribe encounter decisions."""

import time
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Path, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from app.configuration.authority import AUTHORITY_KERNEL_SETTINGS
from app.configuration.scribe_encounters import SCRIBE_ENCOUNTER_SETTINGS
from app.routes.dependencies.view_as import deny_view_as_browser_activity
from app.services import authority, scribe_encounters
from app.services.companion_records import (
    RecordBudget,
    RecordDeadline,
    RecordUnavailable,
)
from app.services.provider_record_authority import ProviderRecordScopeDenied
from app.services.scribe_encounter_contract import REQUEST_ADAPTER
from app.services.superadmin_capability import SuperadminCapabilityError
from app.services.view_as import ViewAsError
from app.utils.encryption import DecryptionError, EncryptionError

from activemodel.session_manager import get_session
from activemodel.types import TypeIDType
from app.models.doctor import Doctor
from app.models.scribe_capture_binding import ScribeCaptureProviderLink
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import col, select

router = APIRouter()


class PrivateNoteView(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(repr=False)
    version: int
    updated_at: str | None


class PrivateNoteSave(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    change_key: UUID
    expected_version: int = Field(ge=0, strict=True)
    text: str = Field(max_length=2000, pattern=r"^[^\x00]*$", repr=False)


def browser_workflow(request, appointment_id, action, **fields):
    deny_view_as_browser_activity(request)
    if getattr(request.state, "view_as", None) is not None:
        raise HTTPException(403, "This action is unavailable during read-only view-as.")
    doctor: Doctor = request.state.doctor
    payload = getattr(getattr(request.state, "auth_state", None), "payload", None)
    if (
        not isinstance(payload, dict)
        or doctor.clerk_id != payload.get("sub")
        or any(
            not isinstance(payload.get(key), str) or not payload[key]
            for key in ("iss", "sub", "sid")
        )
    ):
        raise HTTPException(401, "A verified sign-in session is required.")
    with get_session() as session:
        links = session.exec(
            select(ScribeCaptureProviderLink).where(
                ScribeCaptureProviderLink.doctor_id == doctor.id,
                col(ScribeCaptureProviderLink.revoked_at).is_(None),
            )
        ).all()
        if len(links) != 1:
            raise HTTPException(404, "Visit unavailable")
        link = links[0]
        # Derived references are hints only. encounter_workflow re-resolves and
        # locks the current actor/practice/tenant/visit; no browser authority.
        value = REQUEST_ADAPTER.validate_python(
            dict(
                action=action,
                appointment_ref=str(appointment_id),
                request_nonce=str(uuid4()),
                expected_actor_ref=str(link.actor_id),
                expected_tenant_ref=str(link.tenant_id),
                **fields,
            )
        )
    try:
        return scribe_encounters.encounter_workflow(
            issuer=payload["iss"],
            subject=payload["sub"],
            session_id=payload["sid"],
            request=value,
            settings_provider=lambda: SCRIBE_ENCOUNTER_SETTINGS,
            authority_settings=AUTHORITY_KERNEL_SETTINGS,
            deadline=RecordBudget(time.monotonic() + 15),
        )
    except scribe_encounters.EncounterConflict:
        raise HTTPException(
            409, "This visit changed. Reload it before saving."
        ) from None
    except (
        authority.AuthorityKernelError,
        ProviderRecordScopeDenied,
        SuperadminCapabilityError,
        ViewAsError,
    ):
        raise HTTPException(404, "Visit unavailable") from None
    except (
        RecordDeadline,
        RecordUnavailable,
        EncryptionError,
        DecryptionError,
        SQLAlchemyError,
    ):
        raise HTTPException(503, "Visit temporarily unavailable") from None


@router.get("/notes/{appointment_id}/private-note")
def scribe_private_note_read(
    request: Request, response: Response, appointment_id: Annotated[TypeIDType, Path()]
) -> PrivateNoteView:
    response.headers["Cache-Control"] = "no-store"
    result = browser_workflow(request, appointment_id, "private_note_read")
    return PrivateNoteView.model_validate(result["private_note"])


@router.put("/notes/{appointment_id}/private-note")
def scribe_private_note_save(
    request: Request,
    response: Response,
    appointment_id: Annotated[TypeIDType, Path()],
    body: PrivateNoteSave,
) -> PrivateNoteView:
    response.headers["Cache-Control"] = "no-store"
    result = browser_workflow(
        request, appointment_id, "private_note_save", **body.model_dump(mode="json")
    )
    return PrivateNoteView.model_validate(result["private_note"])
