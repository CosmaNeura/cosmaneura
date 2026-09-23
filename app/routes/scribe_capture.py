"""Bounded, default-off Core facade for creator-bound Scribe capture."""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator

import app.services.scribe_capture_scope as capture_scope
from app.configuration.authority import (
    AUTHORITY_KERNEL_SETTINGS,
    AuthorityKernelSettings,
)
from app.configuration.scribe_capture import (
    SCRIBE_CAPTURE_SETTINGS,
    ScribeCaptureSettings,
)
from app.routes.authority_errors import agent_problem_response
from app.routes.dependencies.entra_service import (
    EntraServicePrincipal,
    authenticate_scribe_capture_request,
)
from app.services import authority, encounter_prep
from app.services import scribe_capture_admission as admission
from app.services import scribe_capture_gateway as gateway
from app.services import scribe_capture_note_edits as note_edits
from app.services import scribe_capture_notes as notes
from app.services.scribe_capture_notebook import ScratchNotes
from app.services.scribe_capture_protocol import CaptureOperation
from app.services.scribe_configured_capture_contract import ConfiguredCaptureSelection
from app.services.scribe_workflow import (
    ScribeWorkflowClient,
    build_scribe_workflow_client,
)

from sqlalchemy.exc import SQLAlchemyError

capture_api_app = APIRouter()
MAX_BODY_BYTES = 2048
NOTE_REQUEST_TIMEOUT_SECONDS = 90


class BindingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    workspace_id: UUID
    appointment_ref: str = Field(pattern=r"^appointment_[0-7][0-9a-hjkmnp-tv-z]{25}$")
    request_nonce: UUID


class InteractionHeaders(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    mount_context_token: str = Field(
        min_length=16, max_length=8192, pattern=r"^[!-~]+$"
    )
    interaction_ref: str = Field(
        min_length=16, max_length=160, pattern=r"^[A-Za-z0-9._~-]+$"
    )
    client_turn_id: UUID


class AdmissionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    binding_ref: str = Field(
        pattern=r"^scribe_capture_binding_[0-7][0-9a-hjkmnp-tv-z]{25}$"
    )
    workspace_id: UUID
    request_nonce: UUID
    attestation: admission.ConsentAttestation | None = None
    configured_capture: ConfiguredCaptureSelection | None = None


class OperationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    binding_ref: str = Field(
        pattern=r"^scribe_capture_binding_[0-7][0-9a-hjkmnp-tv-z]{25}$"
    )
    workspace_id: UUID
    request_nonce: UUID
    operation: CaptureOperation


class NoteScopeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    binding_ref: str = Field(
        pattern=r"^scribe_capture_binding_[0-7][0-9a-hjkmnp-tv-z]{25}$"
    )
    workspace_id: UUID
    request_nonce: UUID
    expected_session_id: UUID


class PrepRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    binding_ref: str = Field(
        pattern=r"^scribe_capture_binding_[0-7][0-9a-hjkmnp-tv-z]{25}$"
    )
    workspace_id: UUID
    request_nonce: UUID


class NoteRequest(NoteScopeRequest):
    schema_ref: str | None = Field(
        default=None, pattern=r"^doctor_note_schema_[0-7][0-9a-hjkmnp-tv-z]{25}$"
    )
    action: Literal["read", "prepare", "templates", "save", "resolve"]
    edit: note_edits.NoteEdit | None = None
    scratch_notes: ScratchNotes | None = None

    @model_validator(mode="after")
    def template_selection(self) -> NoteRequest:
        if (self.action == "templates") != (self.schema_ref is None):
            raise ValueError("invalid_template_selection")
        if (self.action in {"save", "resolve"}) != (self.edit is not None):
            raise ValueError("invalid_note_edit_selection")
        if self.scratch_notes is not None and (
            self.action not in {"read", "prepare"}
            or self.scratch_notes.workspace_id != self.workspace_id
        ):
            raise ValueError("invalid_notebook_source")
        return self


def get_admission_client_factory() -> Callable[[], ScribeWorkflowClient]:
    return build_scribe_workflow_client


def get_capture_settings() -> ScribeCaptureSettings:
    return SCRIBE_CAPTURE_SETTINGS


def get_capture_authority_settings() -> AuthorityKernelSettings:
    return AUTHORITY_KERNEL_SETTINGS


def _problem(status_code: int) -> JSONResponse:
    response = agent_problem_response(
        status_code=status_code,
        code={
            422: "VALIDATION_FAILED",
            403: "SCOPE_DENIED",
            503: "PLATFORM_UNAVAILABLE",
        }[status_code],
        title="Capture unavailable",
        detail="The capture request could not be authorized or completed.",
    )
    response.headers["X-Cosmaneura-Capture-Version"] = "1"
    return response


def _closed_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


async def _validated_request[RequestBody: BaseModel](
    request: Request,
    body_model: type[RequestBody],
    max_body_bytes: int = MAX_BODY_BYTES,
) -> tuple[RequestBody, InteractionHeaders]:
    headers = request.headers
    names = (
        "content-type",
        "x-cosmaneura-mount-context",
        "x-cosmaneura-interaction-ref",
        "x-cosmaneura-client-turn-id",
        "x-cosmaneura-capture-version",
    )
    if (
        any(len(headers.getlist(name)) != 1 for name in names)
        or headers["content-type"].split(";", 1)[0].strip().lower()
        != "application/json"
        or headers["x-cosmaneura-capture-version"] != "1"
        or request.query_params
        or headers.getlist("content-encoding")
        or len(headers.getlist("content-length")) > 1
    ):
        raise ValueError
    length = headers.get("content-length")
    if length is not None and not 0 <= int(length) <= max_body_bytes:
        raise ValueError
    interaction = InteractionHeaders(
        mount_context_token=headers["x-cosmaneura-mount-context"],
        interaction_ref=headers["x-cosmaneura-interaction-ref"],
        client_turn_id=UUID(headers["x-cosmaneura-client-turn-id"]),
    )
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > max_body_bytes:
            raise ValueError
        body.extend(chunk)
    raw = json.loads(bytes(body), object_pairs_hook=_closed_object)
    return body_model.model_validate(raw), interaction


@capture_api_app.post("/scribe/capture-bindings/resolve", response_model=None)
async def resolve_binding(
    request: Request,
    principal: Annotated[
        EntraServicePrincipal, Depends(authenticate_scribe_capture_request)
    ],
    capture_settings: Annotated[ScribeCaptureSettings, Depends(get_capture_settings)],
    authority_settings: Annotated[
        AuthorityKernelSettings, Depends(get_capture_authority_settings)
    ],
) -> JSONResponse:
    # Workload authentication runs before body parsing. The production mount
    # inherits the Agent API's request-scoped ActiveModel session.
    if not capture_settings.enabled:
        return _problem(503)
    try:
        data, interaction = await _validated_request(request, BindingRequest)
    except (TypeError, ValueError, UnicodeError, RecursionError):
        return _problem(422)
    try:
        snapshot = capture_scope.resolve_capture_binding(
            workload_client_id=principal.client_id,
            **interaction.model_dump(),
            **data.model_dump(),
            settings=authority_settings,
            capture_settings=capture_settings,
        )
    except authority.AuthorityKernelDisabledError:
        return _problem(503)
    except authority.AuthorityKernelError:
        return _problem(403)
    except SQLAlchemyError:
        return _problem(503)
    return JSONResponse(
        {
            "contract_version": "1",
            **{key: str(value) for key, value in asdict(snapshot).items()},
        },
        headers={"Cache-Control": "no-store", "X-Cosmaneura-Capture-Version": "1"},
    )


def _admission_problem(
    status_code: int, code: str, *, retryable: bool = False
) -> JSONResponse:
    response = agent_problem_response(
        status_code=status_code,
        code=code,
        title="Capture admission unavailable",
        detail="The capture admission has not been confirmed.",
        retryable=retryable,
    )
    response.headers["X-Cosmaneura-Capture-Version"] = "1"
    if status_code == 202:
        response.headers["Retry-After"] = "3"
    return response


@capture_api_app.post("/scribe/capture-operations", response_model=None)
async def perform_capture_operation(
    request: Request,
    principal: Annotated[
        EntraServicePrincipal, Depends(authenticate_scribe_capture_request)
    ],
    capture_settings: Annotated[ScribeCaptureSettings, Depends(get_capture_settings)],
    authority_settings: Annotated[
        AuthorityKernelSettings, Depends(get_capture_authority_settings)
    ],
    client_factory: Annotated[
        Callable[[], ScribeWorkflowClient], Depends(get_admission_client_factory)
    ],
) -> JSONResponse:
    if not capture_settings.enabled:
        return _problem(503)
    try:
        if len(request.headers.getlist("idempotency-key")) != 1:
            raise ValueError
        key = request.headers["idempotency-key"]
        if not re.fullmatch(r"[!-~]{8,255}", key):
            raise ValueError
        data, interaction = await _validated_request(
            request, OperationRequest, 4_194_304
        )
    except (TypeError, ValueError, UnicodeError, RecursionError):
        return _problem(422)
    try:
        client = client_factory()
    except ValueError:
        return _problem(503)
    try:
        receipt = await gateway.execute_capture_operation(
            caller=admission.CaptureCaller(
                workload_client_id=principal.client_id,
                **interaction.model_dump(),
                settings=authority_settings,
                capture_settings=capture_settings,
            ),
            binding_ref=data.binding_ref,
            workspace_id=data.workspace_id,
            operation=data.operation,
            client_request_key=key,
            client=client,
            upload_origins=capture_settings.upload_origins,
        )
    except authority.AuthorityKernelDisabledError:
        return _problem(503)
    except authority.AuthorityKernelError:
        return _problem(403)
    except (
        admission.AdmissionConsentRequired,
        admission.AdmissionConsentUnavailable,
        gateway.CaptureOperationUnavailable,
    ):
        return _admission_problem(409, "CAPTURE_OPERATION_UNAVAILABLE")
    except (admission.AdmissionConflict, gateway.CaptureReceiptInvalid):
        return _admission_problem(409, "CAPTURE_OPERATION_CONFLICT")
    except (
        admission.AdmissionError,
        gateway.CaptureOperationUnconfirmed,
        SQLAlchemyError,
    ):
        return _admission_problem(503, "CAPTURE_OPERATION_UNCONFIRMED", retryable=True)
    finally:
        try:
            await client.close()
        except Exception:  # noqa: BLE001, S110 -- cleanup must not replace a durable remote outcome
            pass
    return JSONResponse(
        {
            "contract_version": "1",
            "binding_ref": data.binding_ref,
            "workspace_id": str(data.workspace_id),
            "request_nonce": str(data.request_nonce),
            "operation": data.operation.kind,
            "receipt": receipt.model_dump(mode="json"),
            "checked_at": datetime.now(UTC).isoformat(),
        },
        headers={"Cache-Control": "no-store", "X-Cosmaneura-Capture-Version": "1"},
    )


def _note_problem(status_code: int, code: str) -> JSONResponse:
    response = agent_problem_response(
        status_code=status_code,
        code=code,
        title="Capture draft unavailable",
        detail="The draft could not be confirmed. Saved source and note versions are retained.",
        retryable=status_code == 503,
    )
    response.headers["X-Cosmaneura-Capture-Version"] = "1"
    return response


@capture_api_app.post("/scribe/encounter-context", response_model=None)
async def read_encounter_context(
    request: Request,
    principal: Annotated[
        EntraServicePrincipal, Depends(authenticate_scribe_capture_request)
    ],
    capture_settings: Annotated[ScribeCaptureSettings, Depends(get_capture_settings)],
    authority_settings: Annotated[
        AuthorityKernelSettings, Depends(get_capture_authority_settings)
    ],
) -> JSONResponse:
    if not capture_settings.enabled or not capture_settings.prep_enabled:
        return _problem(503)
    try:
        data, interaction = await _validated_request(request, PrepRequest)
    except (TypeError, ValueError, UnicodeError, RecursionError):
        return _problem(422)
    try:
        receipt = encounter_prep.read_encounter_prep(
            caller=admission.CaptureCaller(
                workload_client_id=principal.client_id,
                **interaction.model_dump(),
                settings=authority_settings,
                capture_settings=capture_settings,
            ),
            binding_ref=data.binding_ref,
            workspace_id=data.workspace_id,
        )
    except authority.AuthorityKernelDisabledError:
        return _problem(503)
    except authority.AuthorityKernelError:
        return _problem(403)
    except (SQLAlchemyError, ValueError):
        return _problem(503)
    return JSONResponse(
        {
            "contract_version": "1",
            "binding_ref": data.binding_ref,
            "workspace_id": str(data.workspace_id),
            "request_nonce": str(data.request_nonce),
            "checked_at": datetime.now(UTC).isoformat(),
            "receipt": receipt.model_dump(mode="json"),
        },
        headers={"Cache-Control": "no-store", "X-Cosmaneura-Capture-Version": "1"},
    )


@capture_api_app.post("/scribe/capture-notes", response_model=None)
async def prepare_or_read_note(
    request: Request,
    principal: Annotated[
        EntraServicePrincipal, Depends(authenticate_scribe_capture_request)
    ],
    capture_settings: Annotated[ScribeCaptureSettings, Depends(get_capture_settings)],
    authority_settings: Annotated[
        AuthorityKernelSettings, Depends(get_capture_authority_settings)
    ],
    client_factory: Annotated[
        Callable[[], ScribeWorkflowClient], Depends(get_admission_client_factory)
    ],
) -> JSONResponse:
    if not capture_settings.enabled or not capture_settings.notes_enabled:
        return _note_problem(503, "CAPTURE_NOTES_UNAVAILABLE")
    try:
        data, interaction = await _validated_request(
            request, NoteRequest, notes.MAX_MODEL_BYTES + 8192
        )
    except (TypeError, ValueError, UnicodeError, RecursionError):
        return _note_problem(422, "VALIDATION_FAILED")
    try:
        client = client_factory()
    except ValueError:
        return _note_problem(503, "CAPTURE_NOTES_UNAVAILABLE")
    try:
        caller = admission.CaptureCaller(
            workload_client_id=principal.client_id,
            **interaction.model_dump(),
            settings=authority_settings,
            capture_settings=capture_settings,
        )
        async with asyncio.timeout(NOTE_REQUEST_TIMEOUT_SECONDS):
            if data.action == "templates":
                receipt = await notes.capture_note_templates(
                    caller=caller,
                    binding_ref=data.binding_ref,
                    workspace_id=data.workspace_id,
                    expected_session_id=data.expected_session_id,
                    client=client,
                )
            elif data.action in {"save", "resolve"}:
                assert data.schema_ref is not None and data.edit is not None
                operation = (
                    note_edits.save_capture_note
                    if data.action == "save"
                    else note_edits.resolve_capture_note_save
                )
                receipt = await operation(
                    caller=caller,
                    binding_ref=data.binding_ref,
                    workspace_id=data.workspace_id,
                    expected_session_id=data.expected_session_id,
                    schema_ref=data.schema_ref,
                    edit=data.edit,
                    client=client,
                )
            else:
                assert data.schema_ref is not None
                receipt = await notes.capture_note(
                    caller=caller,
                    binding_ref=data.binding_ref,
                    workspace_id=data.workspace_id,
                    expected_session_id=data.expected_session_id,
                    schema_ref=data.schema_ref,
                    prepare=data.action == "prepare",
                    client=client,
                    scratch_notes=data.scratch_notes,
                )
    except (authority.AuthorityKernelDisabledError, notes.CaptureNoteDisabled):
        return _note_problem(503, "CAPTURE_NOTES_UNAVAILABLE")
    except authority.AuthorityKernelError:
        return _note_problem(403, "SCOPE_DENIED")
    except (
        admission.AdmissionConsentRequired,
        admission.AdmissionConsentUnavailable,
        gateway.CaptureOperationUnavailable,
    ):
        return _note_problem(409, "CAPTURE_SOURCE_UNAVAILABLE")
    except (
        notes.CaptureNoteConflict,
        admission.AdmissionConflict,
        gateway.CaptureReceiptInvalid,
    ):
        return _note_problem(409, "CAPTURE_NOTE_CONFLICT")
    except (
        notes.CaptureNoteUnconfirmed,
        TimeoutError,
        admission.AdmissionError,
        gateway.CaptureOperationUnconfirmed,
        SQLAlchemyError,
    ):
        return _note_problem(503, "CAPTURE_NOTE_UNCONFIRMED")
    finally:
        try:
            await client.close()
        except Exception:  # noqa: BLE001, S110 -- cleanup cannot replace a saved result
            pass
    return JSONResponse(
        {
            "contract_version": "1",
            "binding_ref": data.binding_ref,
            "workspace_id": str(data.workspace_id),
            "request_nonce": str(data.request_nonce),
            "action": data.action,
            "receipt": receipt.model_dump(mode="json"),
            "checked_at": datetime.now(UTC).isoformat(),
        },
        headers={"Cache-Control": "no-store", "X-Cosmaneura-Capture-Version": "1"},
    )


@capture_api_app.post("/scribe/capture-admissions", response_model=None)
async def admit_session(
    request: Request,
    principal: Annotated[
        EntraServicePrincipal, Depends(authenticate_scribe_capture_request)
    ],
    capture_settings: Annotated[ScribeCaptureSettings, Depends(get_capture_settings)],
    authority_settings: Annotated[
        AuthorityKernelSettings, Depends(get_capture_authority_settings)
    ],
    client_factory: Annotated[
        Callable[[], ScribeWorkflowClient], Depends(get_admission_client_factory)
    ],
) -> JSONResponse:
    if not capture_settings.enabled:
        return _problem(503)
    try:
        if len(request.headers.getlist("idempotency-key")) != 1:
            raise ValueError
        key = request.headers["idempotency-key"]
        if not re.fullmatch(r"[!-~]{8,255}", key):
            raise ValueError
        data, interaction = await _validated_request(request, AdmissionRequest)
    except (TypeError, ValueError, UnicodeError, RecursionError):
        return _problem(422)
    try:
        client = client_factory()
    except ValueError:
        return _problem(503)
    try:
        snapshot = await admission.admit_capture(
            caller=admission.CaptureCaller(
                workload_client_id=principal.client_id,
                **interaction.model_dump(),
                settings=authority_settings,
                capture_settings=capture_settings,
            ),
            binding_ref=data.binding_ref,
            workspace_id=data.workspace_id,
            client_request_key=key,
            attestation=data.attestation,
            configured_capture=data.configured_capture,
            client=client,
        )
    except authority.AuthorityKernelDisabledError:
        return _problem(503)
    except authority.AuthorityKernelError:
        return _problem(403)
    except admission.AdmissionConsentRequired:
        return _admission_problem(409, "CAPTURE_CONSENT_REQUIRED")
    except admission.AdmissionConsentUnavailable:
        return _admission_problem(409, "CAPTURE_CONSENT_UNAVAILABLE")
    except admission.AdmissionConflict:
        return _admission_problem(409, "CAPTURE_REQUEST_CONFLICT")
    except admission.AdmissionInProgress:
        return _admission_problem(202, "CAPTURE_IN_PROGRESS", retryable=True)
    except (admission.AdmissionUnconfirmed, SQLAlchemyError):
        return _admission_problem(503, "CAPTURE_ADMISSION_UNCONFIRMED", retryable=True)
    finally:
        try:
            await client.close()
        except Exception:  # noqa: BLE001, S110 -- cleanup must not erase the durable outcome
            pass
    return JSONResponse(
        {
            "contract_version": "1",
            **{key: str(value) for key, value in asdict(snapshot).items()},
            "request_nonce": str(data.request_nonce),
            "checked_at": datetime.now(UTC).isoformat(),
        },
        headers={"Cache-Control": "no-store", "X-Cosmaneura-Capture-Version": "1"},
    )
