"""Dual-authenticated native activation; no body-supplied authority or browser state."""

import asyncio
import json
from contextvars import Context
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.responses import JSONResponse, Response

from app.configuration.authority import AuthorityKernelSettings
from app.configuration.scribe_activation import (
    SCRIBE_ACTIVATION_SETTINGS,
    ScribeActivationSettings,
)
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
from app.services import companion_records
from app.services.scribe_activation import ActivationDenied, ActivationSetupRequired
from app.services.scribe_activation_contract import ActivationRequest
from app.services.scribe_native_activation import execute_activation

from sqlalchemy.exc import SQLAlchemyError

PATH = "/service/v1/agent/scribe/activation"
router = APIRouter()


def activation_settings() -> ScribeActivationSettings:
    return SCRIBE_ACTIVATION_SETTINGS


async def _body(request: Request) -> tuple[ActivationRequest, UUID]:
    headers = request.headers
    if (
        headers.getlist("x-cosmaneura-record-version") != ["1"]
        or len(headers.getlist("content-type")) != 1
        or headers["content-type"].split(";", 1)[0].strip().lower()
        != "application/json"
        or request.query_params
        or headers.getlist("content-encoding")
        or len(headers.getlist("content-length")) > 1
        or headers.getlist("cookie")
        or len(headers.getlist("idempotency-key")) != 1
    ):
        raise ValueError
    key_text = headers["idempotency-key"]
    key = UUID(key_text)
    if not key.int or str(key) != key_text:
        raise ValueError
    length = headers.get("content-length")
    if length is not None and (
        not length.isascii()
        or not length.isdigit()
        or len(length) > 4
        or int(length) > 1024
    ):
        raise ValueError
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > 1024:
            raise ValueError
        data.extend(chunk)
    if length is not None and len(data) != int(length):
        raise ValueError
    payload = ActivationRequest.model_validate(
        json.loads(
            bytes(data).decode("utf-8"),
            object_pairs_hook=_closed_pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
        )
    )
    return payload, key


@router.post(PATH, response_model=None, include_in_schema=False)
async def scribe_activation_request(
    request: Request,
    principal: Annotated[EntraServicePrincipal, Depends(authenticate_native_workload)],
    identity: Annotated[
        VerifiedClerkIdentity, Depends(authenticate_embed_platform_session)
    ],
    settings: Annotated[ScribeActivationSettings, Depends(activation_settings)],
    auth: Annotated[AuthorityKernelSettings, Depends(get_authority_kernel_settings)],
) -> Response:
    deadline = getattr(request.state, "companion_record_deadline", None)
    if not settings.enabled or not isinstance(deadline, companion_records.RecordBudget):
        return problem(503)
    if (
        view_as_browser_session_present(request)
        or request.headers.getlist("cookie")
        or any(
            name in request.headers
            for name in (
                "x-cosmaneura-mount-context",
                "x-cosmaneura-interaction-ref",
                "x-cosmaneura-client-turn-id",
            )
        )
    ):
        return problem(403)
    if request.headers.getlist("x-cosmaneura-record-version") != ["1"]:
        return problem(426)
    try:
        payload, key = await _body(request)
    except (ValueError, TypeError, UnicodeError, RecursionError):
        return problem(422)
    try:
        result = await asyncio.to_thread(
            Context().run,
            execute_activation,
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
        if result.request_nonce != payload.request_nonce:
            raise companion_records.RecordUnavailable
        return Response(
            result.model_dump_json(),
            media_type="application/json",
            headers={"Cache-Control": "no-store", "X-Cosmaneura-Record-Version": "1"},
        )
    except ActivationSetupRequired:
        return JSONResponse(
            {
                "type": "scribe_account_setup_required",
                "status": 409,
                "setup": "practice",
                "request_nonce": str(payload.request_nonce),
            },
            status_code=409,
            media_type="application/problem+json",
            headers={"Cache-Control": "no-store", "X-Cosmaneura-Record-Version": "1"},
        )
    except ActivationDenied:
        return problem(403)
    except companion_records.RecordDeadline:
        return problem(504)
    except (
        SQLAlchemyError,
        companion_records.RecordUnavailable,
        ValueError,
        TypeError,
    ):
        return problem(503)


def install(app: FastAPI) -> None:
    app.include_router(router)
    app.add_middleware(RecordBoundary, path=PATH)
