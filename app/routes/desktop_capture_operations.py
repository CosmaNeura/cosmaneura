"""Main-process-only desktop capture operation routes.

Start and heartbeat requests re-resolve their bearer against current server
authority. Stop is a bounded safety exception that accepts only an exact issued
bearer/session/lease/capability binding and can only narrow state. The desktop
main process supplies no tenant, actor, consent, lease, or provider authority in
the body.
"""

from __future__ import annotations

import re
from typing import Annotated, Literal
from uuid import UUID

from fastapi import (
    APIRouter,
    Body,
    Depends,
    Header,
    HTTPException,
    Path,
    Request,
    Response,
)
from pydantic import BaseModel, ConfigDict, Field
from starlette import status

from app.configuration.desktop_capture import (
    DesktopCaptureSettings,
    get_desktop_capture_settings,
)
from app.routes.desktop_capture_contract import desktop_error_responses
from app.services.capture_coordination import (
    DESKTOP_SOURCE,
    CaptureCoordinationConflict,
    CaptureCoordinationDenied,
    CaptureCoordinationUnavailable,
    CaptureOutcomeUncertain,
    authorize_segment_start,
)
from app.services.capture_launch_auth import (
    CaptureLaunchDenied,
    CaptureLaunchDisabled,
    CaptureLaunchUnavailable,
    ResolvedDesktopAuthority,
    ResolvedDesktopStopAuthority,
    resolve_desktop_authority,
    resolve_desktop_control_authority,
    resolve_desktop_stop_authority,
)
from app.services.capture_lifecycle import (
    CaptureLifecycleConflict,
    CaptureLifecycleDenied,
    CaptureLifecycleReceipt,
    CaptureLifecycleUnavailable,
    record_capture_heartbeat,
    request_capture_stop,
)
from app.services.capture_provider import CaptureProvider

from activemodel.types import TypeIDType

_BEARER = re.compile(r"^(?i:Bearer) ([A-Za-z0-9_-]{43,128})$", re.ASCII)
_IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9._~-]{8,255}$", re.ASCII)

desktop_capture_operations_app = APIRouter(
    prefix="/desktop/v1",
    tags=["desktop-capture"],
)


class StartAuthorizationRequest(BaseModel):
    """An intentionally empty request body.

    The caller cannot assert actor, tenant, consent, lease, or provider data.
    """

    model_config = ConfigDict(extra="forbid")


class CaptureCapabilityResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    capability_id: str = Field(alias="capabilityId")
    secret: str
    expires_at: str = Field(alias="expiresAt")


class StartAuthorityDecisionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_version: Literal[1] = Field(alias="schemaVersion")
    decision: Literal["authorized"]
    capture_session_id: str = Field(alias="captureSessionId")
    actor_id: str = Field(alias="actorId")
    consent_record_id: str = Field(alias="consentRecordId")
    consent_status: Literal["granted"] = Field(alias="consentStatus")
    session_status: Literal["authorized"] = Field(alias="sessionStatus")
    source: Literal["desktop_companion"]
    source_lease_id: str = Field(alias="sourceLeaseId")
    issued_at: str = Field(alias="issuedAt")
    expires_at: str = Field(alias="expiresAt")
    capture_capability: CaptureCapabilityResponse = Field(alias="captureCapability")


class CaptureLifecycleRequest(BaseModel):
    """The capability identifier is the only client-supplied scope hint."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    capability_id: UUID = Field(alias="capabilityId")


class CaptureLifecycleDecisionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_version: Literal[1] = Field(alias="schemaVersion")
    decision: Literal["accepted"]
    capture_session_id: str = Field(alias="captureSessionId")
    capability_id: str = Field(alias="capabilityId")
    receipt_id: str = Field(alias="receiptId")
    session_status: Literal["recording", "stopping"] = Field(alias="sessionStatus")


def get_desktop_capture_provider() -> CaptureProvider | None:
    """Return no provider until an approved deployment explicitly wires one.

    Tests may override this dependency with an in-memory provider. Merely
    enabling environment flags can never cause a real network call.
    """

    return None


def _unavailable() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Desktop capture is unavailable.",
    )


def _invalid_token() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Desktop authorization is invalid.",
        headers={"WWW-Authenticate": "Bearer"},
    )


def _denied() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Capture start authorization was denied.",
    )


def _conflict() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail="Capture start conflicts with current state.",
    )


def _uncertain() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail="Capture start outcome is pending reconciliation.",
    )


def _lifecycle_denied() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Capture lifecycle request was denied.",
    )


def _lifecycle_conflict() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail="Capture lifecycle request conflicts with current state.",
    )


def _single_header(request: Request, name: str) -> str | None:
    values = request.headers.getlist(name)
    if len(values) != 1:
        return None
    return values[0]


def _bearer_token(request: Request) -> str:
    value = _single_header(request, "authorization")
    match = _BEARER.fullmatch(value or "")
    if match is None:
        raise _invalid_token()
    return match.group(1)


def _idempotency_key(request: Request) -> str:
    value = _single_header(request, "idempotency-key")
    if value is None or _IDEMPOTENCY_KEY.fullmatch(value) is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid service request.",
        )
    return value


@desktop_capture_operations_app.post(
    "/capture-sessions/{capture_session_id}/segments/start-authorizations",
    response_model=StartAuthorityDecisionResponse,
    responses=desktop_error_responses(401, 403, 409, 422, 503),
)
def authorize_desktop_capture_segment_start(
    request: Request,
    capture_session_id: Annotated[TypeIDType, Path()],
    response: Response,
    settings: Annotated[DesktopCaptureSettings, Depends(get_desktop_capture_settings)],
    provider: Annotated[CaptureProvider | None, Depends(get_desktop_capture_provider)],
    authorization_header: Annotated[str | None, Header(alias="Authorization")] = None,
    idempotency_header: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    data: Annotated[StartAuthorizationRequest | None, Body()] = None,
) -> StartAuthorityDecisionResponse:
    """Issue one provider-neutral, short-lived physical-start capability."""

    # Keep both headers in the OpenAPI contract, then inspect the raw header
    # list below so duplicate/coalesced authorization cannot be ambiguous.
    del authorization_header, idempotency_header, data

    # These checks precede token resolution and capture coordination. The
    # default provider dependency is side-effect free and returns ``None``.
    if (
        not settings.enabled
        or not settings.provider_create_enabled
        or not settings.launch_is_configured()
        or provider is None
    ):
        raise _unavailable()

    access_token = _bearer_token(request)
    idempotency_key = _idempotency_key(request)

    try:
        resolved = resolve_desktop_authority(
            access_token=access_token,
            settings=settings,
        )
    except (CaptureLaunchDisabled, CaptureLaunchUnavailable):
        raise _unavailable() from None
    except CaptureLaunchDenied:
        raise _invalid_token() from None

    grant = resolved.grant
    if (
        capture_session_id != resolved.capture_session_id
        or str(resolved.capture_session_id) != grant.capture_session_id
        or str(resolved.actor_id) != grant.actor_id
        or str(resolved.consent_evidence_id) != grant.consent_record_id
        or str(resolved.source_lease_id) != grant.source_lease_id
        or grant.consent_status != "granted"
        or grant.session_status != "authorized"
        or grant.source != DESKTOP_SOURCE
    ):
        raise _denied()

    try:
        authority = authorize_segment_start(
            provider=provider,
            practice_id=resolved.practice_id,
            actor_id=resolved.actor_id,
            capture_session_id=resolved.capture_session_id,
            source_lease_id=resolved.source_lease_id,
            consent_evidence_id=resolved.consent_evidence_id,
            # The coordinator immediately stores only a domain-separated HMAC.
            caller_principal=access_token,
            idempotency_key=idempotency_key,
        )
    except CaptureCoordinationDenied:
        raise _denied() from None
    except CaptureCoordinationConflict:
        raise _conflict() from None
    except CaptureOutcomeUncertain:
        raise _uncertain() from None
    except CaptureCoordinationUnavailable:
        raise _unavailable() from None

    # Do not release a capability if any identity or consent binding changed
    # across the provider-neutral service boundary.
    if (
        authority.capture_session_id != grant.capture_session_id
        or authority.actor_id != grant.actor_id
        or authority.consent_record_id != grant.consent_record_id
        or authority.source_lease_id != grant.source_lease_id
    ):
        raise _denied()

    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return StartAuthorityDecisionResponse.model_validate(authority.as_wire_dict())


def _resolve_heartbeat(
    *,
    request: Request,
    capture_session_id: TypeIDType,
    settings: DesktopCaptureSettings,
) -> tuple[ResolvedDesktopAuthority, str, str]:
    # Provider creation can be disabled while an already-issued capture still
    # needs heartbeats. This path performs no provider call; the global desktop
    # gate and launch configuration remain fail closed.
    if not settings.enabled or not settings.launch_is_configured():
        raise _unavailable()
    access_token = _bearer_token(request)
    idempotency_key = _idempotency_key(request)
    try:
        resolved = resolve_desktop_control_authority(
            access_token=access_token,
            settings=settings,
        )
    except (CaptureLaunchDisabled, CaptureLaunchUnavailable):
        raise _unavailable() from None
    except CaptureLaunchDenied:
        raise _invalid_token() from None

    grant = resolved.grant
    if (
        capture_session_id != resolved.capture_session_id
        or str(resolved.capture_session_id) != grant.capture_session_id
        or str(resolved.actor_id) != grant.actor_id
        or str(resolved.consent_evidence_id) != grant.consent_record_id
        or str(resolved.source_lease_id) != grant.source_lease_id
        or grant.consent_status != "granted"
        or grant.session_status != "authorized"
        or grant.source != DESKTOP_SOURCE
    ):
        raise _lifecycle_denied()
    return resolved, access_token, idempotency_key


def _resolve_stop(
    *,
    request: Request,
    capture_session_id: TypeIDType,
) -> tuple[ResolvedDesktopStopAuthority, str, str]:
    # This path can only narrow an exact issued capability toward stopping.
    # It intentionally remains available after launch/creation rollback,
    # bearer expiry/revocation, or loss of current clinical authority.
    access_token = _bearer_token(request)
    idempotency_key = _idempotency_key(request)
    try:
        resolved = resolve_desktop_stop_authority(access_token=access_token)
    except CaptureLaunchUnavailable:
        raise _unavailable() from None
    except CaptureLaunchDenied:
        raise _invalid_token() from None
    if capture_session_id != resolved.capture_session_id:
        raise _lifecycle_denied()
    return resolved, access_token, idempotency_key


def _lifecycle_response(
    *,
    receipt: CaptureLifecycleReceipt,
    response: Response,
) -> CaptureLifecycleDecisionResponse:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return CaptureLifecycleDecisionResponse.model_validate(receipt.as_wire_dict())


@desktop_capture_operations_app.post(
    "/capture-sessions/{capture_session_id}/heartbeats",
    response_model=CaptureLifecycleDecisionResponse,
    responses=desktop_error_responses(401, 403, 409, 422, 503),
)
def record_desktop_capture_heartbeat(
    request: Request,
    capture_session_id: Annotated[TypeIDType, Path()],
    response: Response,
    data: Annotated[CaptureLifecycleRequest, Body()],
    settings: Annotated[DesktopCaptureSettings, Depends(get_desktop_capture_settings)],
    authorization_header: Annotated[str | None, Header(alias="Authorization")] = None,
    idempotency_header: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> CaptureLifecycleDecisionResponse:
    """Consume the issued start capability and refresh the active lease."""

    del authorization_header, idempotency_header
    resolved, access_token, idempotency_key = _resolve_heartbeat(
        request=request,
        capture_session_id=capture_session_id,
        settings=settings,
    )
    try:
        receipt = record_capture_heartbeat(
            practice_id=resolved.practice_id,
            actor_id=resolved.actor_id,
            capture_session_id=resolved.capture_session_id,
            source_lease_id=resolved.source_lease_id,
            consent_evidence_id=resolved.consent_evidence_id,
            capability_id=data.capability_id,
            caller_principal=access_token,
            idempotency_key=idempotency_key,
        )
    except CaptureLifecycleDenied:
        raise _lifecycle_denied() from None
    except CaptureLifecycleConflict:
        raise _lifecycle_conflict() from None
    except CaptureLifecycleUnavailable:
        raise _unavailable() from None
    return _lifecycle_response(receipt=receipt, response=response)


@desktop_capture_operations_app.post(
    "/capture-sessions/{capture_session_id}/stop-requests",
    response_model=CaptureLifecycleDecisionResponse,
    responses=desktop_error_responses(401, 403, 409, 422, 503),
)
def request_desktop_capture_stop(
    request: Request,
    capture_session_id: Annotated[TypeIDType, Path()],
    response: Response,
    data: Annotated[CaptureLifecycleRequest, Body()],
    authorization_header: Annotated[str | None, Header(alias="Authorization")] = None,
    idempotency_header: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> CaptureLifecycleDecisionResponse:
    """Record a stop request without claiming provider-stop evidence."""

    del authorization_header, idempotency_header
    resolved, access_token, idempotency_key = _resolve_stop(
        request=request,
        capture_session_id=capture_session_id,
    )
    try:
        receipt = request_capture_stop(
            practice_id=resolved.practice_id,
            actor_id=resolved.actor_id,
            capture_session_id=resolved.capture_session_id,
            source_lease_id=resolved.source_lease_id,
            consent_evidence_id=resolved.consent_evidence_id,
            capability_id=data.capability_id,
            caller_principal=access_token,
            idempotency_key=idempotency_key,
        )
    except CaptureLifecycleDenied:
        raise _lifecycle_denied() from None
    except CaptureLifecycleConflict:
        raise _lifecycle_conflict() from None
    except CaptureLifecycleUnavailable:
        raise _unavailable() from None
    return _lifecycle_response(receipt=receipt, response=response)
