"""Dormant dual-authenticated preview/request/own-status; no shared login deletion."""

import asyncio
import json
from contextvars import Context
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.responses import Response

from app.configuration.authority import AuthorityKernelSettings
from app.configuration.scribe_cancellation import SCRIBE_CANCELLATION_SETTINGS
from app.configuration.scribe_native import native_authority_settings
from app.routes.companion_records import RecordBoundary, _closed_pairs, problem
from app.routes.dependencies.entra_service import EntraServicePrincipal
from app.routes.dependencies.view_as import view_as_browser_session_present
from app.routes.embed_context import (
    VerifiedClerkIdentity,
    authenticate_embed_platform_session,
    get_authority_kernel_settings,
)
from app.routes.scribe_native_capture import authenticate_native_workload
from app.services import authority, companion_records, scribe_cancellation
from app.services.scribe_cancellation_contract import (
    REQUEST_ADAPTER,
    AcceptRequest,
    CancellationConflict,
    CancellationDenied,
    CancellationNotFound,
    CancellationSettings,
    CancellationUnavailable,
)
from app.services.superadmin_capability import SuperadminCapabilityError
from app.services.view_as import ViewAsError

from sqlalchemy.exc import SQLAlchemyError

PATH = "/service/v1/agent/scribe/cancellation"
MAX_REQUEST_BYTES = 2048
router = APIRouter()


def cancellation_settings() -> CancellationSettings:
    return SCRIBE_CANCELLATION_SETTINGS


async def _body(request: Request):
    headers = request.headers
    if (
        headers.getlist("x-cosmaneura-record-version") != ["1"]
        or len(headers.getlist("content-type")) != 1
        or headers["content-type"].split(";", 1)[0].strip().lower()
        != "application/json"
        or request.query_params
        or headers.getlist("content-encoding")
        or headers.getlist("cookie")
        or len(headers.getlist("content-length")) > 1
    ):
        raise ValueError
    length = headers.get("content-length")
    if length is not None and (
        not length.isascii()
        or not length.isdigit()
        or len(length) > 4
        or int(length) > MAX_REQUEST_BYTES
    ):
        raise ValueError
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > MAX_REQUEST_BYTES:
            raise ValueError
        data.extend(chunk)
    if length is not None and len(data) != int(length):
        raise ValueError
    payload = REQUEST_ADAPTER.validate_python(
        json.loads(
            bytes(data).decode("utf-8"),
            object_pairs_hook=_closed_pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
        )
    )
    keys = headers.getlist("idempotency-key")
    key = None
    if isinstance(payload, AcceptRequest):
        if len(keys) != 1:
            raise ValueError
        key = UUID(keys[0])
        if str(key) != keys[0] or key.int == 0:
            raise ValueError
    elif keys:
        raise ValueError
    return payload, key


@router.post(PATH, response_model=None, include_in_schema=False)
async def scribe_cancellation_request(
    request: Request,
    principal: Annotated[EntraServicePrincipal, Depends(authenticate_native_workload)],
    identity: Annotated[
        VerifiedClerkIdentity, Depends(authenticate_embed_platform_session)
    ],
    settings: Annotated[CancellationSettings, Depends(cancellation_settings)],
    auth: Annotated[AuthorityKernelSettings, Depends(get_authority_kernel_settings)],
) -> Response:
    deadline = getattr(request.state, "companion_record_deadline", None)
    if not settings.ledger_ready or not isinstance(
        deadline, companion_records.RecordBudget
    ):
        return problem(503)
    if view_as_browser_session_present(request):
        return problem(403)
    if request.headers.getlist("x-cosmaneura-record-version") != ["1"]:
        return problem(426)
    try:
        payload, key = await _body(request)
    except (ValueError, TypeError, UnicodeError, RecursionError):
        return problem(422)
    try:
        companion_records.check_deadline(deadline)
        result = await asyncio.to_thread(
            Context().run,
            scribe_cancellation.execute_cancellation,
            issuer=identity.issuer,
            subject=identity.subject,
            session_id=identity.clerk_session_id,
            request=payload,
            request_key=key,
            settings=settings,
            authority_settings=native_authority_settings(auth),
            deadline=deadline,
        )
        companion_records.check_deadline(deadline)
        if (
            result.action != payload.action
            or result.request_nonce != payload.request_nonce
        ):
            raise CancellationUnavailable
        body = result.model_dump_json().encode()
        if len(body) > 4096:
            raise CancellationUnavailable
        return Response(
            body,
            media_type="application/json",
            headers={"Cache-Control": "no-store", "X-Cosmaneura-Record-Version": "1"},
        )
    except companion_records.RecordDeadline:
        return problem(504)
    except CancellationConflict:
        return problem(409)
    except CancellationNotFound:
        return problem(404)
    except (
        CancellationDenied,
        authority.AuthorityKernelError,
        SuperadminCapabilityError,
        ViewAsError,
    ):
        return problem(403)
    except (
        CancellationUnavailable,
        companion_records.RecordUnavailable,
        SQLAlchemyError,
        ValueError,
        TypeError,
    ):
        return problem(503)


def install(app: FastAPI) -> None:
    app.include_router(router)
    app.add_middleware(RecordBoundary, path=PATH)
