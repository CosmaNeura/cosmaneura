"""Bounded, default-off Core facade for creator-bound Scribe capture."""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator

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
from app.routes.scribe_capture_deadline import CaptureDeadlineRoute
from app.services import authority, encounter_prep
from app.services import scribe_capture_admission as admission
from app.services import scribe_capture_gateway as gateway
from app.services import scribe_capture_note_edits as note_edits
from app.services import scribe_capture_notes as notes
from app.services.scribe_capture_deadline import (
    DeadlineScribeClient,
    check_capture_deadline,
)
from app.services.scribe_capture_note_request import NoteRequest as NoteRequest
from app.services.scribe_capture_note_request import (
    NoteScopeRequest as NoteScopeRequest,
)
from app.services.scribe_capture_protocol import CaptureOperation
from app.services.scribe_configured_capture_contract import ConfiguredCaptureSelection
from app.services.scribe_failure_diagnostics import report_unconfirmed
from app.services.scribe_workflow import (
    ScribeWorkflowClient,
    build_scribe_workflow_client,
)

from sqlalchemy.exc import SQLAlchemyError

capture_api_app = APIRouter(route_class=CaptureDeadlineRoute)
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
    expected_session_id: UUID | None = None
    resume_current: StrictBool = False
    recording_key_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    recover_legacy_request: StrictBool = False

    @model_validator(mode="after")
    def resume_only(self) -> AdmissionRequest:
        if self.recover_legacy_request and (
            self.recording_key_sha256 is None or self.attestation is None
        ):
            raise ValueError("invalid_legacy_recording_recovery")
        admission.validate_resume_request(
            self.expected_session_id,
            self.resume_current,
            self.attestation,
            self.configured_capture,
        )
        if self.recording_key_sha256 is not None and (
            self.expected_session_id is not None or self.resume_current
        ):
            raise ValueError("invalid_recording_selection")
        return self


class OperationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    binding_ref: str = Field(
        pattern=r"^scribe_capture_binding_[0-7][0-9a-hjkmnp-tv-z]{25}$"
    )
    workspace_id: UUID
    request_nonce: UUID
    operation: CaptureOperation
    expected_session_id: UUID | None = None


class PrepRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    binding_ref: str = Field(
        pattern=r"^scribe_capture_binding_[0-7][0-9a-hjkmnp-tv-z]{25}$"
    )
    workspace_id: UUID
    request_nonce: UUID


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
        check_capture_deadline()
        client = DeadlineScribeClient(client_factory())
    except ValueError:
        return _problem(503)
    try:
        receipt = await gateway.execute_capture_operation(
            caller=admission.CaptureCaller(
                workload_client_id=principal.client_id,
                **interaction.model_dump(),
                settings=authority_settings,
                capture_settings=capture_settings,
                selected_session_id=data.expected_session_id,
            ),
            binding_ref=data.binding_ref,
            workspace_id=data.workspace_id,
            operation=data.operation,
            client_request_key=key,
            client=client,
            upload_origins=capture_settings.upload_origins,
        )
    except gateway.CaptureUploadAuthorizationExpired:
        return _admission_problem(409, "UPLOAD_AUTHORIZATION_EXPIRED")
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
    ) as error:
        report_unconfirmed(
            boundary="capture_operation", operation=data.operation.kind, error=error
        )
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
        check_capture_deadline()
        client = DeadlineScribeClient(client_factory())
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
            elif data.action == "template_preference":
                receipt = await notes.capture_template_preference(
                    caller=caller,
                    binding_ref=data.binding_ref,
                    workspace_id=data.workspace_id,
                    expected_session_id=data.expected_session_id,
                    client=client,
                )
            elif data.action in {"save", "resolve", "save_status"}:
                assert data.schema_ref is not None and data.edit is not None
                operation = {
                    "save": note_edits.save_capture_note,
                    "resolve": note_edits.resolve_capture_note_save,
                    "save_status": note_edits.check_capture_note_save,
                }[data.action]
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
        check_capture_deadline()
        client = DeadlineScribeClient(client_factory())
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
            expected_session_id=data.expected_session_id,
            resume_current=data.resume_current,
            recording_key_sha256=data.recording_key_sha256,
            recover_legacy_request=data.recover_legacy_request,
            client=client,
        )
    except authority.AuthorityKernelDisabledError:
        return _problem(503)
    except authority.AuthorityKernelError:
        return _problem(403)
    except admission.AdmissionRecordingConsentRequired:
        return _admission_problem(409, "CAPTURE_RECORDING_CONSENT_REQUIRED")
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
            **{
                key: str(value)
                for key, value in asdict(snapshot).items()
                if key != "configured_capture"
            },
            **(
                {
                    "configured_capture": snapshot.configured_capture.model_dump(
                        mode="json"
                    )
                }
                if snapshot.configured_capture is not None
                else {}
            ),
            "request_nonce": str(data.request_nonce),
            "checked_at": datetime.now(UTC).isoformat(),
        },
        headers={"Cache-Control": "no-store", "X-Cosmaneura-Capture-Version": "1"},
    )
