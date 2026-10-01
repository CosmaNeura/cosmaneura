"""Native receiver facade with unchanged Clerk and both workload policies."""

import json
import re
from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from fastapi.security import HTTPAuthorizationCredentials

from app.configuration.authority import AuthorityKernelSettings
from app.configuration.scribe_capture import ScribeCaptureSettings
from app.configuration.scribe_encounters import ScribeEncounterSettings
from app.routes.authority_errors import agent_problem_response
from app.routes.dependencies.entra_service import (
    EntraServicePrincipal,
    authenticate_agent_api_request,
    authenticate_scribe_capture_request,
)
from app.routes.dependencies.view_as import view_as_browser_session_present
from app.routes.embed_context import (
    VerifiedClerkIdentity,
    authenticate_embed_platform_session,
)
from app.routes.scribe_capture import (
    _closed_object,
    get_admission_client_factory,
    get_capture_authority_settings,
    get_capture_settings,
)
from app.routes.scribe_capture_deadline import CaptureDeadlineRoute
from app.routes.scribe_encounters import encounter_settings
from app.services import authority
from app.services import scribe_capture_admission as admission
from app.services import scribe_capture_gateway as gateway
from app.services.companion_records import RecordUnavailable
from app.services.provider_record_authority import ProviderRecordScopeDenied
from app.services.scribe_capture_deadline import (
    DeadlineScribeClient,
    check_async_capture_deadline,
)
from app.services.scribe_native_capture import (
    NativeCaptureRequest,
    execute_native_capture,
)
from app.services.scribe_workflow import ScribeWorkflowClient
from app.services.superadmin_capability import SuperadminCapabilityError
from app.services.view_as import ViewAsError

from sqlalchemy.exc import SQLAlchemyError

native_capture_api_app = APIRouter(
    prefix="/service/v1/agent", route_class=CaptureDeadlineRoute
)
PATH = "/scribe/native-capture"
MAX_BODY = 4_194_304
MAX_RESPONSE = 32 * 1024 * 1024 + 32_768


def authenticate_native_workload(request: Request) -> EntraServicePrincipal:
    """The Clerk SDK reads Authorization itself; never replace that header."""
    values = request.headers.getlist("x-cosmaneura-workload-authorization")
    if (
        len(values) != 1
        or len(request.headers.getlist("authorization")) != 1
        or not re.fullmatch(r"Bearer [!-~]{16,8192}", values[0])
    ):
        raise HTTPException(401, "Native capture authentication required")
    credentials = HTTPAuthorizationCredentials(
        scheme="Bearer", credentials=values[0][7:]
    )
    agent = authenticate_agent_api_request(credentials)
    capture = authenticate_scribe_capture_request(credentials)
    if agent.client_id != capture.client_id:
        raise HTTPException(403, "Native capture unavailable")
    return capture


def _failure(status: int) -> Response:
    result = agent_problem_response(
        status_code=status,
        code="NATIVE_CAPTURE_UNCONFIRMED",
        title="Capture unavailable",
        detail="The capture request could not be authorized or completed.",
        retryable=False,
    )
    result.headers["X-Cosmaneura-Native-Capture-Version"] = "1"
    return result


async def _body(request: Request) -> tuple[NativeCaptureRequest, str]:
    headers = request.headers
    names = ("content-type", "idempotency-key", "x-cosmaneura-native-capture-version")
    if (
        any(len(headers.getlist(n)) != 1 for n in names)
        or headers["content-type"].split(";", 1)[0].strip().lower()
        != "application/json"
        or headers["x-cosmaneura-native-capture-version"] != "1"
        or not re.fullmatch(r"[A-Za-z0-9._:-]{8,255}", headers["idempotency-key"])
        or request.query_params
        or headers.getlist("content-encoding")
        or headers.getlist("cookie")
        or any(
            n in headers
            for n in (
                "x-cosmaneura-mount-context",
                "x-cosmaneura-interaction-ref",
                "x-cosmaneura-client-turn-id",
            )
        )
        or len(headers.getlist("content-length")) > 1
    ):
        raise ValueError
    length = headers.get("content-length")
    if length is not None and (
        not length.isascii() or not length.isdigit() or not 0 <= int(length) <= MAX_BODY
    ):
        raise ValueError
    body = bytearray()
    async for chunk in request.stream():
        check_async_capture_deadline()
        if len(body) + len(chunk) > MAX_BODY:
            raise ValueError
        body.extend(chunk)
    if length is not None and len(body) != int(length):
        raise ValueError

    def invalid(_value):
        raise ValueError

    raw = json.loads(
        bytes(body), object_pairs_hook=_closed_object, parse_constant=invalid
    )
    return NativeCaptureRequest.model_validate(raw), headers["idempotency-key"]


@native_capture_api_app.post(PATH, response_model=None, include_in_schema=False)
async def native_capture(
    request: Request,
    principal: Annotated[EntraServicePrincipal, Depends(authenticate_native_workload)],
    identity: Annotated[
        VerifiedClerkIdentity, Depends(authenticate_embed_platform_session)
    ],
    settings: Annotated[ScribeCaptureSettings, Depends(get_capture_settings)],
    encounters: Annotated[ScribeEncounterSettings, Depends(encounter_settings)],
    auth: Annotated[AuthorityKernelSettings, Depends(get_capture_authority_settings)],
    client_factory: Annotated[
        Callable[[], ScribeWorkflowClient], Depends(get_admission_client_factory)
    ],
) -> Response:
    if not settings.enabled or not encounters.enabled:
        return _failure(503)
    if view_as_browser_session_present(request):
        return _failure(403)
    try:
        data, key = await _body(request)
    except (TypeError, ValueError, UnicodeError, RecursionError):
        return _failure(422)
    client = None
    try:
        check_async_capture_deadline()
        client = DeadlineScribeClient(client_factory())
        value = await execute_native_capture(
            issuer=identity.issuer,
            subject=identity.subject,
            clerk_session_id=identity.clerk_session_id,
            workload_client_id=principal.client_id,
            data=data,
            key=key,
            authority_settings=auth,
            capture_settings=settings,
            encounter_settings=encounters,
            client=client,
        )
        check_async_capture_deadline()
        raw = json.dumps(
            value, ensure_ascii=False, allow_nan=False, separators=(",", ":")
        ).encode()
        if len(raw) > MAX_RESPONSE:
            raise gateway.CaptureReceiptInvalid
        return Response(
            raw,
            media_type="application/json",
            headers={
                "Cache-Control": "no-store",
                "X-Cosmaneura-Native-Capture-Version": "1",
            },
        )
    except authority.AuthorityKernelDisabledError:
        return _failure(503)
    except (
        authority.AuthorityKernelError,
        ProviderRecordScopeDenied,
        SuperadminCapabilityError,
        ViewAsError,
    ):
        return _failure(403)
    except (
        admission.AdmissionConsentRequired,
        admission.AdmissionConsentUnavailable,
        admission.AdmissionConflict,
        gateway.CaptureOperationUnavailable,
        gateway.CaptureReceiptInvalid,
    ):
        return _failure(409)
    except (
        admission.AdmissionError,
        gateway.CaptureOperationUnconfirmed,
        RecordUnavailable,
        SQLAlchemyError,
        ValueError,
        TypeError,
    ):
        return _failure(503)
    finally:
        if client is not None:
            try:
                await client.close()
            except Exception:  # noqa: BLE001, S110 -- retain the original durable result
                pass
