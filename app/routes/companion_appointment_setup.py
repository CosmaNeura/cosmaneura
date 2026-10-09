"""Authenticated preview and explicit connection of existing appointments."""

import asyncio
import json
from contextvars import Context
from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.responses import Response

from app.configuration.authority import AuthorityKernelSettings
from app.configuration.companion_appointment_setup import (
    COMPANION_APPOINTMENT_SETUP_SETTINGS,
)
from app.routes.companion_records import RecordBoundary, _closed_pairs, problem
from app.routes.dependencies.view_as import view_as_browser_session_present
from app.routes.embed_context import (
    VerifiedClerkIdentity,
    authenticate_embed_platform_session,
    get_authority_kernel_settings,
)
from app.services import authority
from app.services import companion_appointment_setup as service
from app.services.companion_appointment_bindings import DeferredBinding
from app.services.companion_appointment_setup_contract import AppointmentSetupRequest
from app.services.companion_record_contract import MAX_REQUEST_BYTES
from app.services.companion_records import (
    RecordBudget,
    RecordDeadline,
    RecordUnavailable,
)
from app.services.superadmin_capability import SuperadminCapabilityError
from app.services.view_as import ViewAsError

from sqlalchemy.exc import SQLAlchemyError

PATH = "/embed/v1/companion/appointments/connect"
router = APIRouter()


def setup_settings():
    return COMPANION_APPOINTMENT_SETUP_SETTINGS


async def _body(request):
    headers = request.headers
    if (
        any(
            len(headers.getlist(name)) != 1
            for name in ("authorization", "content-type")
        )
        or headers["content-type"].split(";", 1)[0].strip().lower()
        != "application/json"
        or request.query_params
        or headers.getlist("content-encoding")
        or len(headers.getlist("content-length")) > 1
    ):
        raise ValueError("invalid_request")
    length = headers.get("content-length")
    if length is not None and (
        not length.isascii()
        or not length.isdigit()
        or len(length) > 4
        or int(length) > MAX_REQUEST_BYTES
    ):
        raise ValueError("invalid_length")
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > MAX_REQUEST_BYTES:
            raise ValueError("invalid_length")
        data.extend(chunk)
    if length is not None and len(data) != int(length):
        raise ValueError("invalid_length")
    return AppointmentSetupRequest.model_validate(
        json.loads(
            bytes(data).decode("utf-8"),
            object_pairs_hook=_closed_pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(
                ValueError("invalid_number")
            ),
        )
    )


@router.post(PATH, response_model=None, include_in_schema=False)
async def setup_existing_appointments(
    request: Request,
    identity: Annotated[
        VerifiedClerkIdentity, Depends(authenticate_embed_platform_session)
    ],
    auth: Annotated[AuthorityKernelSettings, Depends(get_authority_kernel_settings)],
) -> Response:
    budget = getattr(request.state, "companion_record_deadline", None)
    if not isinstance(budget, RecordBudget) or not setup_settings().enabled:
        return problem(503)
    if request.headers.getlist("x-cosmaneura-appointment-setup-version") != ["1"]:
        return problem(426)
    if view_as_browser_session_present(request):
        return problem(403)
    try:
        data = await _body(request)
    except (ValueError, TypeError, UnicodeError, RecursionError):
        return problem(422)
    try:
        result = await asyncio.to_thread(
            Context().run,
            service.setup_appointments,
            issuer=identity.issuer,
            subject=identity.subject,
            session_id=identity.clerk_session_id,
            request=data,
            settings_provider=setup_settings,
            authority_settings=auth,
            deadline=budget,
        )
        return Response(
            result.model_dump_json(),
            media_type="application/json",
            headers={
                "Cache-Control": "no-store",
                "X-Cosmaneura-Appointment-Setup-Version": "1",
            },
        )
    except RecordDeadline:
        return problem(504)
    except service.AppointmentSetupChanged:
        return problem(409)
    except authority.AuthorityKernelDisabledError:
        return problem(503)
    except (authority.AuthorityKernelError, SuperadminCapabilityError, ViewAsError):
        return problem(403)
    except (RecordUnavailable, DeferredBinding, SQLAlchemyError, ValueError, TypeError):
        return problem(503)


def install(app: FastAPI):
    app.include_router(router)
    app.add_middleware(RecordBoundary, path=PATH)
