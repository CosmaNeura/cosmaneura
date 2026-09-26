"""Bounded signed-service reads; no public/browser mutation endpoint."""

import asyncio
import json
import time
from collections.abc import Awaitable
from contextvars import Context
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.responses import JSONResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.configuration.authority import (
    AUTHORITY_KERNEL_SETTINGS,
    AuthorityKernelSettings,
)
from app.configuration.companion_records import (
    COMPANION_RECORD_SETTINGS,
    CompanionRecordSettings,
)
from app.configuration.scribe_capture import (
    SCRIBE_CAPTURE_SETTINGS,
    ScribeCaptureSettings,
)
from app.routes.authority_errors import agent_problem_response
from app.routes.dependencies.entra_service import (
    EntraServicePrincipal,
    authenticate_agent_api_request,
)
from app.services import authority, companion_records
from app.services.companion_record_contract import (
    DEADLINE_SECONDS,
    MAX_REQUEST_BYTES,
    MAX_RESPONSE_BYTES,
    RecordInteraction,
    RecordRequest,
    RecordResponse,
)

from sqlalchemy.exc import SQLAlchemyError

PATH = "/records/encounter/read"
DEFAULT_PREFIX = "/service/v1/agent"
ERROR_SEND_SECONDS = 0.25
router = APIRouter()


def record_settings() -> CompanionRecordSettings:
    return COMPANION_RECORD_SETTINGS


def authority_settings() -> AuthorityKernelSettings:
    return AUTHORITY_KERNEL_SETTINGS


def capture_settings() -> ScribeCaptureSettings:
    return SCRIBE_CAPTURE_SETTINGS


def problem(status: int) -> JSONResponse:
    code = {
        403: "REQUEST_DENIED",
        422: "INVALID_REQUEST",
        426: "CONTRACT_REQUIRED",
        504: "DEADLINE_EXCEEDED",
    }.get(status, "SERVICE_UNAVAILABLE")
    response = agent_problem_response(
        status_code=status,
        code=code,
        title="CosmaNeura record unavailable",
        detail="The current record could not be confirmed.",
        retryable=status in {503, 504},
    )
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Cosmaneura-Record-Version"] = "1"
    return response


class RecordBoundary:
    """Deadline begins before auth/dependencies; public output is buffered/bounded."""

    def __init__(self, app: ASGIApp, *, path: str):
        self.app, self.path = app, path
        self._settling: set[asyncio.Future[None]] = set()

    def _cancel(self, task: asyncio.Future[None]) -> None:
        # Cancellation-resistant framework cleanup may finish privately. Keep
        # ownership and observe its exception without extending the public wait.
        self._settling.add(task)
        task.cancel()

        def settled(future: asyncio.Future[None]) -> None:
            self._settling.discard(future)
            if not future.cancelled():
                future.exception()

        task.add_done_callback(settled)

    async def _within(self, work: Awaitable[None], seconds: float) -> None:
        task = asyncio.ensure_future(work)
        try:
            if seconds <= 0:
                self._cancel(task)
                raise TimeoutError
            done, _ = await asyncio.wait({task}, timeout=seconds)
            if not done:
                self._cancel(task)
                raise TimeoutError
            task.result()
        except asyncio.CancelledError:
            self._cancel(task)
            raise

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["path"] != self.path:
            await self.app(scope, receive, send)
            return
        deadline = companion_records.RecordBudget(time.monotonic() + DEADLINE_SECONDS)
        scope.setdefault("state", {})["companion_record_deadline"] = deadline
        messages: list[Message] = []
        length = 0

        async def buffered(message: Message) -> None:
            nonlocal length
            companion_records.check_deadline(deadline)
            if message["type"] == "http.response.body":
                length += len(message.get("body", b""))
                if length > MAX_RESPONSE_BYTES:
                    raise companion_records.RecordUnavailable
            messages.append(message)

        async def fail(status: int) -> None:
            deadline.cancel()
            messages.clear()
            error_deadline = time.monotonic() + ERROR_SEND_SECONDS
            output_open = True

            async def static_send(message: Message) -> None:
                # A sender may suppress cancellation. Never admit its next
                # static chunk after grace or reuse this grace for private data.
                if not output_open or time.monotonic() >= error_deadline:
                    raise TimeoutError
                await send(message)

            try:
                await self._within(
                    problem(status)(scope, receive, static_send),
                    error_deadline - time.monotonic(),
                )
            except TimeoutError:
                return
            finally:
                output_open = False

        try:
            try:
                await self._within(
                    self.app(scope, receive, buffered),
                    deadline.expires_at - time.monotonic(),
                )
                companion_records.check_deadline(deadline)
            except (TimeoutError, companion_records.RecordDeadline):
                await fail(504)
                return
            except companion_records.RecordUnavailable:
                await fail(503)
                return
            except Exception:  # noqa: BLE001 -- value-free private boundary.
                await fail(503)
                return
            for message in messages:
                if message["type"] == "http.response.start":
                    headers = [
                        (k, v)
                        for k, v in message.get("headers", [])
                        if k.lower()
                        not in {b"cache-control", b"x-cosmaneura-record-version"}
                    ]
                    message = {
                        **message,
                        "headers": [
                            *headers,
                            (b"cache-control", b"no-store"),
                            (b"x-cosmaneura-record-version", b"1"),
                        ],
                    }
                try:
                    companion_records.check_deadline(deadline)
                    await self._within(
                        send(message), deadline.expires_at - time.monotonic()
                    )
                except (TimeoutError, companion_records.RecordDeadline):
                    # Headers may already be admitted: never append a new status
                    # or private chunk. The client must reject incomplete output.
                    return
        finally:
            deadline.cancel()
            messages.clear()


def _closed_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_field")
        result[key] = value
    return result


async def read_body(request: Request) -> tuple[RecordRequest, RecordInteraction]:
    headers = request.headers
    exact = (
        "authorization",
        "content-type",
        "x-cosmaneura-mount-context",
        "x-cosmaneura-interaction-ref",
        "x-cosmaneura-client-turn-id",
    )
    if (
        any(len(headers.getlist(name)) != 1 for name in exact)
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
    interaction = RecordInteraction(
        mount_context_token=headers["x-cosmaneura-mount-context"],
        interaction_ref=headers["x-cosmaneura-interaction-ref"],
        client_turn_id=UUID(headers["x-cosmaneura-client-turn-id"]),
    )
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > MAX_REQUEST_BYTES:
            raise ValueError("request_capacity_exceeded")
        data.extend(chunk)
    if length is not None and len(data) != int(length):
        raise ValueError("invalid_length")
    value = json.loads(
        bytes(data).decode("utf-8"),
        object_pairs_hook=_closed_pairs,
        parse_constant=lambda _: (_ for _ in ()).throw(ValueError("invalid_number")),
    )
    return RecordRequest.model_validate(value), interaction


@router.post(PATH, response_model=None, include_in_schema=False)
async def read_encounter_record(
    request: Request,
    principal: Annotated[
        EntraServicePrincipal, Depends(authenticate_agent_api_request)
    ],
    settings: Annotated[CompanionRecordSettings, Depends(record_settings)],
    auth: Annotated[AuthorityKernelSettings, Depends(authority_settings)],
    capture: Annotated[ScribeCaptureSettings, Depends(capture_settings)],
) -> Response:
    deadline = getattr(request.state, "companion_record_deadline", None)
    # Omitting the required registration boundary must fail closed.
    if not isinstance(deadline, companion_records.RecordBudget) or not settings.enabled:
        return problem(503)
    if request.headers.getlist("x-cosmaneura-record-version") != ["1"]:
        return problem(426)
    try:
        data, interaction = await read_body(request)
    except (ValueError, TypeError, UnicodeError, RecursionError):
        return problem(422)
    try:
        companion_records.check_deadline(deadline)
        # Fresh context prevents an abandoned read from borrowing a request
        # Session that dependency cleanup could close while its worker runs.
        context = Context()
        result = await asyncio.to_thread(
            context.run,
            companion_records.read_record,
            principal_client_id=principal.client_id,
            interaction=interaction,
            request=data,
            settings=settings,
            authority_settings=auth,
            capture_settings=capture,
            deadline=deadline,
        )
        companion_records.check_deadline(deadline)
        result = RecordResponse.model_validate(result.model_dump())
        if (result.binding_ref, result.workspace_id, result.request_nonce) != (
            data.binding_ref,
            data.workspace_id,
            data.request_nonce,
        ):
            raise companion_records.RecordUnavailable
        body = result.model_dump_json().encode("utf-8")
        if len(body) > MAX_RESPONSE_BYTES:
            raise companion_records.RecordUnavailable
        return Response(
            body,
            media_type="application/json",
            headers={"Cache-Control": "no-store", "X-Cosmaneura-Record-Version": "1"},
        )
    except companion_records.RecordDeadline:
        return problem(504)
    except authority.AuthorityKernelError:
        return problem(403)
    except (
        companion_records.RecordUnavailable,
        SQLAlchemyError,
        ValueError,
        TypeError,
    ):
        return problem(503)


def install(app: FastAPI, *, prefix: str = DEFAULT_PREFIX) -> None:
    """Root's agent parent and this standalone mount use the same private path."""
    if prefix != DEFAULT_PREFIX:
        raise ValueError("invalid_record_prefix")
    app.include_router(router, prefix=prefix)
    app.add_middleware(RecordBoundary, path=prefix + PATH)
