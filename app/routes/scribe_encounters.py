"""Bounded normal Clerk-session workflow; support viewing grants no authority."""

import asyncio
import json
from contextvars import Context
from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.responses import Response

from app.configuration.authority import AuthorityKernelSettings
from app.configuration.scribe_encounters import (
    SCRIBE_ENCOUNTER_SETTINGS,
    ScribeEncounterSettings,
)
from app.routes.companion_records import RecordBoundary, _closed_pairs, problem
from app.routes.dependencies.view_as import view_as_browser_session_present
from app.routes.embed_context import (
    VerifiedClerkIdentity,
    authenticate_embed_platform_session,
    get_authority_kernel_settings,
)
from app.services import authority, companion_records, scribe_encounters
from app.services.provider_record_authority import ProviderRecordScopeDenied
from app.services.scribe_encounter_contract import REQUEST_ADAPTER, AddBirthDateRequest, CreateRequest
from app.services.superadmin_capability import SuperadminCapabilityError
from app.services.view_as import ViewAsError

from sqlalchemy.exc import SQLAlchemyError

PATH = "/embed/v1/scribe/encounters"
router = APIRouter()


def encounter_settings() -> ScribeEncounterSettings:
    return SCRIBE_ENCOUNTER_SETTINGS


async def read_body(request: Request):
    headers = request.headers
    if (
        any(len(headers.getlist(n)) != 1 for n in ("authorization", "content-type"))
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
        or int(length) > 4096
    ):
        raise ValueError("invalid_length")
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > 4096:
            raise ValueError("request_too_large")
        data.extend(chunk)
    if length is not None and len(data) != int(length):
        raise ValueError("invalid_length")
    value = json.loads(
        data.decode("utf-8"),
        object_pairs_hook=_closed_pairs,
        parse_constant=lambda _: (_ for _ in ()).throw(ValueError("invalid_number")),
    )
    result = REQUEST_ADAPTER.validate_python(value)
    if isinstance(result, CreateRequest) and headers.getlist("idempotency-key") != [
        str(result.creation_key)
    ]:
        raise ValueError("creation_key_required")
    if isinstance(result, AddBirthDateRequest) and headers.getlist("idempotency-key") != [str(result.change_key)]:
        raise ValueError("change_key_required")
    return result


@router.post(PATH, response_model=None, include_in_schema=False)
async def scribe_encounter_workflow(
    request: Request,
    identity: Annotated[
        VerifiedClerkIdentity, Depends(authenticate_embed_platform_session)
    ],
    settings: Annotated[ScribeEncounterSettings, Depends(encounter_settings)],
    auth: Annotated[AuthorityKernelSettings, Depends(get_authority_kernel_settings)],
) -> Response:
    deadline = getattr(request.state, "companion_record_deadline", None)
    if not isinstance(deadline, companion_records.RecordBudget) or not settings.enabled:
        return problem(503)
    if request.headers.getlist("x-cosmaneura-scribe-encounter-version") != ["1"]:
        return problem(426)
    if view_as_browser_session_present(request):
        return problem(403)
    try:
        data = await read_body(request)
    except (ValueError, TypeError, UnicodeError, RecursionError):
        return problem(422)
    try:
        result = await asyncio.to_thread(
            Context().run,
            scribe_encounters.encounter_workflow,
            issuer=identity.issuer,
            subject=identity.subject,
            session_id=identity.clerk_session_id,
            request=data,
            settings_provider=lambda: settings,
            authority_settings=auth,
            deadline=deadline,
        )
        companion_records.check_deadline(deadline)
        body = json.dumps(
            result, ensure_ascii=False, allow_nan=False, separators=(",", ":")
        ).encode()
        if len(body) > 1_048_576:
            raise companion_records.RecordUnavailable
        return Response(
            body,
            media_type="application/json",
            headers={
                "Cache-Control": "no-store",
                "X-Cosmaneura-Scribe-Encounter-Version": "1",
            },
        )
    except scribe_encounters.EncounterConflict:
        return problem(409)
    except companion_records.RecordDeadline:
        return problem(504)
    except authority.AuthorityKernelDisabledError:
        return problem(503)
    except (
        authority.AuthorityKernelError,
        SuperadminCapabilityError,
        ViewAsError,
        ProviderRecordScopeDenied,
    ):
        return problem(403)
    except ValueError:
        return problem(422)
    except (companion_records.RecordUnavailable, SQLAlchemyError, TypeError):
        return problem(503)


def install(app: FastAPI) -> None:
    app.include_router(router)
    app.add_middleware(RecordBoundary, path=PATH)
