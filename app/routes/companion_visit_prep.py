"""Explicit physician package display, not a conversational or generation API."""

import asyncio
import json
from contextvars import Context
from time import monotonic
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse, Response
from pydantic import AwareDatetime, Field

from app.configuration import clerk, service_auth
from app.configuration.service_auth import CompanionAuthenticationRejected
from app.routes.companion_records import RecordBoundary, _closed_pairs
from app.routes.dependencies.view_as import view_as_browser_session_present
from app.routes.visit_prep_synthesis import (
    VisitPrepSynthesisResponse,
    _synthesis_response,
)
from app.services import authority, visit_prep_companion, visit_prep_companion_list
from app.services.companion_record_contract import Closed, Digest
from app.services.companion_records import (
    RecordBudget,
    RecordDeadline,
    RecordUnavailable,
    check_deadline,
)
from app.services.visit_prep_companion import (
    MAX_RESPONSE_BYTES,
    PATH,
    PreparationRef,
    VisitPrepCompanionDenied,
    VisitPrepCompanionRequest,
)
from app.services.visit_prep_synthesis_contract import (
    select_patient_sources,
    validate_selection,
)
from app.services.visit_preps import VisitPrepConflict, VisitPrepNotFound

MAX_REQUEST_BYTES = 2048
HEADERS = {
    "Cache-Control": "no-store",
    "Referrer-Policy": "no-referrer",
    "X-Cosmaneura-Visit-Prep-Version": "1",
}
router = APIRouter()


def problem(status: int) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"detail": "Visit Prep is unavailable."},
        headers=HEADERS,
    )


class VisitPrepCompanionResponse(Closed):
    contract_version: Literal["1"] = "1"
    preparation_ref: PreparationRef
    connection_sha256: Digest
    workspace_id: UUID
    request_nonce: UUID
    checked_at: AwareDatetime
    package: VisitPrepSynthesisResponse = Field(repr=False)


class VisitPrepSelection(Closed):
    preparation_ref: PreparationRef
    recipient_first_name: str = Field(min_length=1, max_length=120, repr=False)
    recipient_role: Literal["patient", "caregiver"]
    scheduled_at: AwareDatetime
    timezone_name: str = Field(min_length=1, max_length=64)
    lifecycle_state: Literal[
        "invited", "confirmed", "in_progress", "completed", "misdirected"
    ]
    chart_linked: bool
    available_to_open: bool
    created_at: AwareDatetime | None


class VisitPrepSelectionResponse(Closed):
    contract_version: Literal["1"] = "1"
    connection_sha256: Digest
    workspace_id: UUID
    request_nonce: UUID
    checked_at: AwareDatetime
    preparations: list[VisitPrepSelection] = Field(max_length=20)
    next_before_ref: PreparationRef | None


def serialize_selections(request, result, at):
    return (
        VisitPrepSelectionResponse(
            connection_sha256=request.connection_sha256,
            workspace_id=request.workspace_id,
            request_nonce=request.request_nonce,
            checked_at=at,
            preparations=[
                VisitPrepSelection(
                    preparation_ref=str(p.id),
                    recipient_first_name=recipient[0],
                    recipient_role=recipient[1],
                    scheduled_at=p.scheduled_at,
                    timezone_name=p.timezone_name,
                    lifecycle_state=p.lifecycle_state,
                    chart_linked=p.patient_id is not None,
                    available_to_open=p.revoked_at is None
                    and p.lifecycle_state in {"confirmed", "in_progress", "completed"},
                    created_at=p.created_at,
                )
                for p, recipient in zip(
                    result.preparations, result.recipients, strict=True
                )
            ],
            next_before_ref=str(result.next_before_id)
            if result.next_before_id
            else None,
        )
        .model_dump_json()
        .encode("utf-8")
    )


def serialize_package(request, result, at) -> bytes:
    selection = select_patient_sources(result.package)
    validate_selection(selection)
    package = _synthesis_response(result)
    if (
        package.sources.package_id != request.preparation_ref
        or package.sources.preparation.id != request.preparation_ref
        or (package.status == "ready") != (package.brief is not None)
        or (
            package.status != "ready"
            and any(
                value is not None
                for value in (package.artifact_id, package.version, package.recorded_at)
            )
        )
    ):
        raise RecordUnavailable
    receipt = VisitPrepCompanionResponse(
        preparation_ref=request.preparation_ref,
        connection_sha256=request.connection_sha256,
        workspace_id=request.workspace_id,
        request_nonce=request.request_nonce,
        checked_at=at,
        package=package,
    )
    body = receipt.model_dump_json().encode("utf-8")
    if len(body) > MAX_RESPONSE_BYTES:
        raise RecordUnavailable
    VisitPrepCompanionResponse.model_validate_json(body)
    return body


def _bearer(headers, name):
    values = headers.getlist(name)
    if len(values) != 1:
        raise CompanionAuthenticationRejected(401)
    value = values[0]
    scheme, separator, token = value.partition(" ")
    if (
        not separator
        or scheme.lower() != "bearer"
        or not token
        or len(value) > 8192
        or not value.isascii()
        or any(character.isspace() for character in token)
    ):
        raise CompanionAuthenticationRejected(401)
    return token


async def read_body(request, model: type[Closed] = VisitPrepCompanionRequest):
    headers = request.headers
    if (
        request.scope.get("query_string")
        or any(
            headers.getlist(name) != ["application/json"]
            for name in ("content-type", "accept")
        )
        or headers.getlist("accept-encoding") != ["identity"]
        or headers.getlist("content-encoding")
        or len(headers.getlist("content-length")) > 1
    ):
        raise ValueError("invalid_request")
    length = headers.get("content-length")
    if length is not None:
        if not length.isascii() or not length.isdigit() or len(length) > 4:
            raise ValueError("invalid_request")
        if int(length) > MAX_REQUEST_BYTES:
            raise OverflowError
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > MAX_REQUEST_BYTES:
            raise OverflowError
        body.extend(chunk)
    if length is not None and int(length) != len(body):
        raise ValueError("invalid_request")
    return model.model_validate(
        json.loads(
            body.decode("utf-8"),
            object_pairs_hook=_closed_pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(
                ValueError("invalid_number")
            ),
        )
    )


@router.post(PATH, response_model=None, include_in_schema=False)
@router.post(
    visit_prep_companion_list.PATH, response_model=None, include_in_schema=False
)
async def read_companion_visit_prep(request: Request) -> Response:
    deadline = getattr(request.state, "visit_prep_companion_deadline", None)
    settings = service_auth.COMPANION_VISIT_PREP_AUTH_SETTINGS
    if (
        not isinstance(deadline, RecordBudget)
        or not settings.enabled
        or not settings.is_complete()
    ):
        return problem(503)
    if request.headers.getlist("x-cosmaneura-visit-prep-version") != ["1"]:
        return problem(426)
    if view_as_browser_session_present(request):
        return problem(403)
    try:
        workload_token = _bearer(request.headers, "authorization")
        physician_token = _bearer(
            request.headers, "x-cosmaneura-provider-authorization"
        )
        is_list = request.scope["path"] == visit_prep_companion_list.PATH
        data = await read_body(
            request,
            visit_prep_companion_list.VisitPrepCompanionListRequest
            if is_list
            else VisitPrepCompanionRequest,
        )
        check_deadline(deadline)
    except CompanionAuthenticationRejected as error:
        return problem(error.status_code)
    except OverflowError:
        return problem(413)
    except (ValueError, TypeError, UnicodeError, RecursionError):
        return problem(422)
    except RecordDeadline:
        return problem(504)

    try:

        def execute():
            workload = service_auth.verify_visit_prep_workload(
                workload_token, remaining_seconds=check_deadline(deadline) / 1000
            )
            physician = clerk.verify_visit_prep_physician(
                physician_token, remaining_seconds=check_deadline(deadline) / 1000
            )
            if is_list:
                return visit_prep_companion_list.read_visit_prep_companion_list(
                    workload=workload,
                    physician=physician,
                    request=data,
                    deadline=deadline,
                    serialize=lambda result, at: serialize_selections(data, result, at),
                )
            if not isinstance(data, VisitPrepCompanionRequest):
                raise RecordUnavailable
            return visit_prep_companion.read_visit_prep_companion(
                workload=workload,
                physician=physician,
                request=data,
                deadline=deadline,
                serialize=lambda result, at: serialize_package(data, result, at),
            )

        body = await asyncio.to_thread(Context().run, execute)
        check_deadline(deadline)
        return Response(body, media_type="application/json", headers=HEADERS)
    except CompanionAuthenticationRejected as error:
        return problem(error.status_code)
    except (RecordDeadline, TimeoutError):
        return problem(504)
    except (
        VisitPrepCompanionDenied,
        VisitPrepNotFound,
        VisitPrepConflict,
        authority.AuthorityRequestDeniedError,
    ):
        return problem(403)
    except Exception:
        # SDK/storage/serialization diagnostics never reach response or logs.
        return problem(503)


class VisitPrepBoundary(RecordBoundary):
    """Reuse bounded settling-task ownership, with Visit Prep's private wire."""

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["path"].rstrip("/") != self.path:
            await self.app(scope, receive, send)
            return
        deadline = RecordBudget(monotonic() + 15)
        scope.setdefault("state", {})["visit_prep_companion_deadline"] = deadline
        start, body, finished = None, bytearray(), False

        async def buffer(message):
            nonlocal start, finished
            check_deadline(deadline)
            if message["type"] == "http.response.start":
                if start is not None:
                    raise RecordUnavailable
                start = message
            elif message["type"] == "http.response.body":
                if (
                    start is None
                    or finished
                    or len(body) + len(message.get("body", b""))
                    > (
                        visit_prep_companion_list.MAX_RESPONSE_BYTES
                        if self.path == visit_prep_companion_list.PATH
                        else MAX_RESPONSE_BYTES
                    )
                ):
                    raise RecordUnavailable
                body.extend(message.get("body", b""))
                finished = not message.get("more_body", False)
            else:
                raise RecordUnavailable

        async def fail(status):
            deadline.cancel()
            body.clear()
            try:
                await self._within(problem(status)(scope, receive, send), 0.25)
            except TimeoutError:
                pass

        try:
            if scope["path"] != self.path or scope["method"] != "POST":
                await fail(422)
                return
            try:
                await self._within(
                    self.app(scope, receive, buffer), deadline.expires_at - monotonic()
                )
                check_deadline(deadline)
                if start is None or not finished:
                    raise RecordUnavailable
                status = start["status"]
                if status != 200:
                    await fail(
                        status if status in {401, 403, 413, 422, 426, 503, 504} else 503
                    )
                    return
                model = (
                    VisitPrepSelectionResponse
                    if self.path == visit_prep_companion_list.PATH
                    else VisitPrepCompanionResponse
                )
                model.model_validate_json(body)
            except (TimeoutError, RecordDeadline):
                await fail(504)
                return
            except Exception:
                await fail(503)
                return
            headers = [
                (key.lower().encode(), value.encode()) for key, value in HEADERS.items()
            ]
            headers.extend(
                [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                ]
            )
            await self._within(
                send(
                    {"type": "http.response.start", "status": 200, "headers": headers}
                ),
                deadline.expires_at - monotonic(),
            )
            check_deadline(deadline)
            await self._within(
                send({"type": "http.response.body", "body": bytes(body)}),
                deadline.expires_at - monotonic(),
            )
        except (TimeoutError, RecordDeadline):
            # No replacement status/private chunk after output has begun.
            return
        finally:
            deadline.cancel()
            body.clear()


def install(app: FastAPI):
    app.include_router(router, include_in_schema=False)
    app.add_middleware(VisitPrepBoundary, path=PATH)
    app.add_middleware(VisitPrepBoundary, path=visit_prep_companion_list.PATH)
