"""Unauthenticated-by-account, bearer-authorized Visit Prep interview API."""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, Coroutine, Mapping
from datetime import datetime
from typing import Annotated, Any, Literal, TypeVar

from fastapi import (
    APIRouter,
    Depends,
    Header,
    HTTPException,
    Request,
    Response,
    Security,
    status,
)
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse
from starlette.routing import Match
from starlette.types import Receive, Scope, Send

from app import log
from app.services import visit_prep_interviews as interview_service
from app.services import visit_prep_rate_limits as rate_limits
from app.services.visit_prep_interview_contract import (
    InterviewMutationResult,
    InterviewState,
    ResponseInput,
    VisitPrepInterviewConflict,
    VisitPrepInterviewUnavailable,
    VisitPrepInterviewValidation,
)

from activemodel.session_manager import aglobal_session
from sqlalchemy.exc import SQLAlchemyError

_MAX_BODY_BYTES = 10 * 1024
_BEARER = re.compile(r"^Bearer (vp1_[A-Za-z0-9_-]{43})$")
_DOCUMENTED_BEARER = HTTPBearer(
    auto_error=False,
    scheme_name="VisitPrepBearer",
    description="One canonical Visit Prep bearer supplied only in this header.",
)

_UNAVAILABLE_DETAIL = "Visit Prep link is unavailable."
_VALIDATION_DETAIL = "Visit Prep request is invalid."
_CONFLICT_DETAIL = "Visit Prep request conflicts with its current state."
_RATE_LIMIT_DETAIL = "Too many Visit Prep requests. Try again later."
_TEMPORARY_DETAIL = "Visit Prep is temporarily unavailable."
_PUBLIC_PREFIX = "/external/v1/visit-prep"

_ResultT = TypeVar("_ResultT")


def _privacy_headers() -> dict[str, str]:
    return {
        "Cache-Control": "no-store",
        "Referrer-Policy": "no-referrer",
    }


def _boundary_cors_headers(request: Request) -> dict[str, str]:
    """Mirror the configured CORS origin on errors emitted outside CORS."""

    origins = request.headers.getlist("origin")
    allowed_origins = getattr(
        request.app.state,
        "visit_prep_allowed_origins",
        frozenset(),
    )
    if len(origins) != 1 or origins[0] not in allowed_origins:
        return {}
    return {
        "Access-Control-Allow-Origin": origins[0],
        "Access-Control-Allow-Credentials": "true",
        "Vary": "Origin",
    }


def _boundary_error_response(
    request: Request,
    *,
    status_code: int,
    detail: str,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"detail": detail},
        headers={
            **_privacy_headers(),
            **_boundary_cors_headers(request),
            **(headers or {}),
        },
    )


class _SensitivePublicVisitPrepRoute(APIRoute):
    """Bound bodies and redact all validation output for the public surface."""

    def get_route_handler(
        self,
    ) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        route_handler = super().get_route_handler()

        async def bounded_route_handler(request: Request) -> Response:
            try:
                await run_in_threadpool(_enforce_network_rate_limit_once, request)
            except (
                rate_limits.RateLimited,
                rate_limits.RateLimitUnavailable,
            ) as exc:
                _record_rate_limit_failure(exc, stage="network")
                public_error = _rate_limit_error(exc)
                return JSONResponse(
                    status_code=public_error.status_code,
                    content={"detail": public_error.detail},
                    headers={
                        **_privacy_headers(),
                        **(public_error.headers or {}),
                    },
                )
            except Exception:
                log.warning(
                    "Visit Prep public network limiter failed unexpectedly",
                    action="visit_prep.public_network_limit_unexpected_failure",
                )
                return JSONResponse(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    content={"detail": _TEMPORARY_DETAIL},
                    headers=_privacy_headers(),
                )
            if request.query_params:
                return JSONResponse(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    content={"detail": _VALIDATION_DETAIL},
                    headers=_privacy_headers(),
                )
            if request.method in {"POST", "PUT", "PATCH"}:
                declared_lengths = request.headers.getlist("content-length")
                if len(declared_lengths) > 1:
                    return JSONResponse(
                        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                        content={"detail": _VALIDATION_DETAIL},
                        headers=_privacy_headers(),
                    )
                declared_length = declared_lengths[0] if declared_lengths else None
                try:
                    parsed_length = (
                        int(declared_length) if declared_length is not None else None
                    )
                    if parsed_length is not None and parsed_length < 0:
                        return JSONResponse(
                            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            content={"detail": _VALIDATION_DETAIL},
                            headers=_privacy_headers(),
                        )
                    if parsed_length is not None and parsed_length > _MAX_BODY_BYTES:
                        return JSONResponse(
                            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                            content={"detail": _VALIDATION_DETAIL},
                            headers=_privacy_headers(),
                        )
                except ValueError:
                    return JSONResponse(
                        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                        content={"detail": _VALIDATION_DETAIL},
                        headers=_privacy_headers(),
                    )

                body = bytearray()
                async for chunk in request.stream():
                    if len(body) + len(chunk) > _MAX_BODY_BYTES:
                        return JSONResponse(
                            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                            content={"detail": _VALIDATION_DETAIL},
                            headers=_privacy_headers(),
                        )
                    body.extend(chunk)
                # Starlette caches request bodies on this attribute; setting it
                # lets FastAPI parse the already-bounded bytes without a second
                # receive or an unbounded allocation.
                request._body = bytes(body)  # noqa: SLF001

            try:
                response = await route_handler(request)
            except RequestValidationError:
                return JSONResponse(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    content={"detail": _VALIDATION_DETAIL},
                    headers=_privacy_headers(),
                )
            except HTTPException as exc:
                details = {
                    404: _UNAVAILABLE_DETAIL,
                    409: _CONFLICT_DETAIL,
                    413: _VALIDATION_DETAIL,
                    422: _VALIDATION_DETAIL,
                    429: _RATE_LIMIT_DETAIL,
                    503: _TEMPORARY_DETAIL,
                }
                status_code = exc.status_code if exc.status_code in details else 503
                return JSONResponse(
                    status_code=status_code,
                    content={"detail": details[status_code]},
                    headers={
                        **_privacy_headers(),
                        **(
                            {"Retry-After": str(rate_limits.WINDOW_SECONDS)}
                            if status_code == 429
                            else {}
                        ),
                    },
                )
            except Exception:
                log.warning(
                    "Visit Prep public request failed unexpectedly",
                    action="visit_prep.public_unexpected_failure",
                )
                return JSONResponse(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    content={"detail": _TEMPORARY_DETAIL},
                    headers=_privacy_headers(),
                )
            response.headers.update(_privacy_headers())
            return response

        return bounded_route_handler


class _AnyMethodSensitivePublicVisitPrepRoute(_SensitivePublicVisitPrepRoute):
    """Let the final catch-all normalize every otherwise unsupported method."""

    def matches(self, scope: Scope) -> tuple[Match, Scope]:
        match, child_scope = super().matches(scope)
        if match is Match.PARTIAL:
            return Match.FULL, child_scope
        return match, child_scope

    async def handle(self, scope: Scope, receive: Receive, send: Send) -> None:
        await self.app(scope, receive, send)


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class VisitPrepPublicErrorResponse(_StrictModel):
    detail: str


class VisitPrepPublicChoiceResponse(_StrictModel):
    value: str
    label: str
    hint: str | None


class VisitPrepPublicQuestionResponse(_StrictModel):
    key: str
    position: int
    prompt: str
    helper_text: str | None
    kind: Literal["story", "list", "choice"]
    choices: list[VisitPrepPublicChoiceResponse]


class VisitPrepPublicStateResponse(_StrictModel):
    status: Literal[
        "confirmation_required",
        "in_progress",
        "ready_to_complete",
        "completed",
    ]
    physician_display_name: str | None
    practice_display_name: str | None
    scheduled_at: datetime | None
    timezone_name: str | None
    submitted_questions: int
    maximum_questions: int
    current_question: VisitPrepPublicQuestionResponse | None


class VisitPrepPublicMutationResponse(_StrictModel):
    interview: VisitPrepPublicStateResponse
    disposition: Literal["confirmed", "accepted", "replayed", "completed"]


class VisitPrepWrongRecipientResponse(_StrictModel):
    disposition: Literal["reported"]


class VisitPrepConfirmationRequest(_StrictModel):
    first_name: str = Field(min_length=1, max_length=120, repr=False)
    role: Literal["patient", "caregiver"]


class VisitPrepResponseRequest(_StrictModel):
    question_key: str = Field(min_length=1, max_length=64)
    response_kind: Literal["answered", "skipped"]
    answer: str | None = Field(default=None, repr=False)


_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    status.HTTP_404_NOT_FOUND: {
        "model": VisitPrepPublicErrorResponse,
        "description": "The Visit Prep link is unavailable.",
    },
    status.HTTP_409_CONFLICT: {
        "model": VisitPrepPublicErrorResponse,
        "description": "The request conflicts with current interview state.",
    },
    status.HTTP_413_REQUEST_ENTITY_TOO_LARGE: {
        "model": VisitPrepPublicErrorResponse,
        "description": "The request body is too large.",
    },
    status.HTTP_422_UNPROCESSABLE_ENTITY: {
        "model": VisitPrepPublicErrorResponse,
        "description": "The request is invalid.",
    },
    status.HTTP_429_TOO_MANY_REQUESTS: {
        "model": VisitPrepPublicErrorResponse,
        "description": "The public rate limit was exceeded.",
    },
    status.HTTP_503_SERVICE_UNAVAILABLE: {
        "model": VisitPrepPublicErrorResponse,
        "description": "The public interview service is unavailable.",
    },
}


def _bearer_from_request(request: Request) -> str | None:
    values = request.headers.getlist("authorization")
    if len(values) != 1:
        return None
    match = _BEARER.fullmatch(values[0])
    return match.group(1) if match is not None else None


def _network_signal(request: Request) -> str:
    # Consume only the ASGI server's normalized client identity. The application
    # never parses forwarding headers itself; the deployment must verify and
    # allowlist its exact trusted proxy chain before public activation.
    if request.client is None or not request.client.host:
        return "unavailable"
    return request.client.host


def _rate_limit_error(exc: Exception) -> HTTPException:
    if isinstance(exc, rate_limits.RateLimited):
        return HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=_RATE_LIMIT_DETAIL,
            headers={"Retry-After": str(rate_limits.WINDOW_SECONDS)},
        )
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail=_TEMPORARY_DETAIL,
    )


def _record_rate_limit_failure(
    exc: Exception,
    *,
    stage: Literal["network", "credential"],
) -> None:
    if isinstance(exc, rate_limits.RateLimited):
        message = "Visit Prep public request rate limited"
        action = "visit_prep.public_rate_limited"
    else:
        message = "Visit Prep public rate-limit store unavailable"
        action = "visit_prep.public_rate_limit_store_unavailable"
    # This event deliberately excludes the network signal and bearer. The stage
    # is a fixed code value, not request data.
    log.warning(message, action=action, stage=stage)


def _enforce_network_rate_limit_once(request: Request) -> None:
    if getattr(request.state, "visit_prep_network_rate_limited", False):
        return
    rate_limits.enforce_network_rate_limit(
        network_signal=_network_signal(request),
    )
    request.state.visit_prep_network_rate_limited = True


async def visit_prep_public_network_boundary(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    """Rate-limit the entire public prefix before CORS can short-circuit it."""

    path = request.url.path
    if path != _PUBLIC_PREFIX and not path.startswith(f"{_PUBLIC_PREFIX}/"):
        return await call_next(request)
    try:
        await run_in_threadpool(_enforce_network_rate_limit_once, request)
    except (
        rate_limits.RateLimited,
        rate_limits.RateLimitUnavailable,
    ) as exc:
        _record_rate_limit_failure(exc, stage="network")
        public_error = _rate_limit_error(exc)
        return _boundary_error_response(
            request,
            status_code=public_error.status_code,
            detail=str(public_error.detail),
            headers=public_error.headers,
        )
    except Exception:
        log.warning(
            "Visit Prep public network limiter failed unexpectedly",
            action="visit_prep.public_network_limit_unexpected_failure",
        )
        return _boundary_error_response(
            request,
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=_TEMPORARY_DETAIL,
        )
    try:
        response = await call_next(request)
    except Exception:
        log.warning(
            "Visit Prep public boundary failed unexpectedly",
            action="visit_prep.public_boundary_unexpected_failure",
        )
        return _boundary_error_response(
            request,
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=_TEMPORARY_DETAIL,
        )
    response.headers.update(_privacy_headers())
    return response


def require_public_visit_prep_bearer(
    request: Request,
    documented_bearer: Annotated[
        HTTPAuthorizationCredentials | None,
        Security(_DOCUMENTED_BEARER),
    ],
) -> str:
    """Rate-limit, validate, then return one header-only canonical bearer."""

    del documented_bearer
    try:
        _enforce_network_rate_limit_once(request)
    except (
        rate_limits.RateLimited,
        rate_limits.RateLimitUnavailable,
    ) as exc:
        _record_rate_limit_failure(exc, stage="network")
        raise _rate_limit_error(exc) from None

    if not interview_service.public_interview_api_enabled():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_UNAVAILABLE_DETAIL,
        )

    network_signal = _network_signal(request)
    access_token = _bearer_from_request(request)
    if access_token is None:
        try:
            rate_limits.enforce_credential_rate_limits(
                network_signal=network_signal,
                access_token=None,
                confirmation=request.url.path.endswith(
                    ("/confirm", "/wrong-recipient")
                ),
            )
        except (
            rate_limits.RateLimited,
            rate_limits.RateLimitUnavailable,
        ) as exc:
            _record_rate_limit_failure(exc, stage="credential")
            raise _rate_limit_error(exc) from None
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_UNAVAILABLE_DETAIL,
        )

    try:
        rate_limits.enforce_credential_rate_limits(
            network_signal=network_signal,
            access_token=access_token,
            confirmation=request.url.path.endswith(("/confirm", "/wrong-recipient")),
        )
    except (
        rate_limits.RateLimited,
        rate_limits.RateLimitUnavailable,
    ) as exc:
        _record_rate_limit_failure(exc, stage="credential")
        raise _rate_limit_error(exc) from None
    return access_token


visit_prep_interviews_api_app = APIRouter(
    prefix="/external/v1/visit-prep",
    tags=["visit-prep-public"],
    dependencies=[Depends(aglobal_session)],
    route_class=_SensitivePublicVisitPrepRoute,
    responses=_ERROR_RESPONSES,
)


def _invoke(operation: Callable[[], _ResultT]) -> _ResultT:
    try:
        return operation()
    except VisitPrepInterviewUnavailable:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_UNAVAILABLE_DETAIL,
        ) from None
    except VisitPrepInterviewValidation:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=_VALIDATION_DETAIL,
        ) from None
    except VisitPrepInterviewConflict:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=_CONFLICT_DETAIL,
        ) from None
    except SQLAlchemyError:
        log.warning(
            "Visit Prep public persistence unavailable",
            action="visit_prep.public_persistence_unavailable",
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=_TEMPORARY_DETAIL,
        ) from None


def _state_response(state: InterviewState) -> VisitPrepPublicStateResponse:
    question = state.current_question
    return VisitPrepPublicStateResponse(
        status=state.status,
        physician_display_name=state.physician_display_name,
        practice_display_name=state.practice_display_name,
        scheduled_at=state.scheduled_at,
        timezone_name=state.timezone_name,
        submitted_questions=state.submitted_questions,
        maximum_questions=state.maximum_questions,
        current_question=(
            VisitPrepPublicQuestionResponse(
                key=question.key,
                position=question.position,
                prompt=question.prompt,
                helper_text=question.helper_text,
                kind=question.kind,
                choices=[
                    VisitPrepPublicChoiceResponse(
                        value=choice.value,
                        label=choice.label,
                        hint=choice.hint,
                    )
                    for choice in question.choices
                ],
            )
            if question is not None
            else None
        ),
    )


def _mutation_response(
    result: InterviewMutationResult,
) -> VisitPrepPublicMutationResponse:
    return VisitPrepPublicMutationResponse(
        interview=_state_response(result.interview),
        disposition=result.disposition,
    )


@visit_prep_interviews_api_app.get(
    "",
    name="visit_prep_public_resume",
    response_model=VisitPrepPublicStateResponse,
)
def visit_prep_public_resume(
    access_token: Annotated[str, Depends(require_public_visit_prep_bearer)],
) -> VisitPrepPublicStateResponse:
    return _state_response(
        _invoke(lambda: interview_service.get_interview(access_token=access_token))
    )


@visit_prep_interviews_api_app.post(
    "/confirm",
    name="visit_prep_public_confirm",
    response_model=VisitPrepPublicMutationResponse,
)
def visit_prep_public_confirm(
    data: VisitPrepConfirmationRequest,
    access_token: Annotated[str, Depends(require_public_visit_prep_bearer)],
) -> VisitPrepPublicMutationResponse:
    return _mutation_response(
        _invoke(
            lambda: interview_service.confirm_interview(
                access_token=access_token,
                first_name=data.first_name,
                role=data.role,
            )
        )
    )


@visit_prep_interviews_api_app.post(
    "/responses",
    name="visit_prep_public_submit_response",
    response_model=VisitPrepPublicMutationResponse,
)
def visit_prep_public_submit_response(
    request: Request,
    data: VisitPrepResponseRequest,
    documented_idempotency_key: Annotated[
        str,
        Header(alias="Idempotency-Key"),
    ],
    access_token: Annotated[str, Depends(require_public_visit_prep_bearer)],
) -> VisitPrepPublicMutationResponse:
    del documented_idempotency_key
    idempotency_headers = request.headers.getlist("idempotency-key")
    if len(idempotency_headers) != 1:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=_VALIDATION_DETAIL,
        )
    return _mutation_response(
        _invoke(
            lambda: interview_service.submit_response(
                access_token=access_token,
                idempotency_key=idempotency_headers[0],
                response=ResponseInput(
                    question_key=data.question_key,
                    response_kind=data.response_kind,
                    answer=data.answer,
                ),
            )
        )
    )


@visit_prep_interviews_api_app.post(
    "/complete",
    name="visit_prep_public_complete",
    response_model=VisitPrepPublicMutationResponse,
)
def visit_prep_public_complete(
    access_token: Annotated[str, Depends(require_public_visit_prep_bearer)],
) -> VisitPrepPublicMutationResponse:
    return _mutation_response(
        _invoke(lambda: interview_service.complete_interview(access_token=access_token))
    )


@visit_prep_interviews_api_app.post(
    "/wrong-recipient",
    name="visit_prep_public_wrong_recipient",
    response_model=VisitPrepWrongRecipientResponse,
)
def visit_prep_public_wrong_recipient(
    access_token: Annotated[str, Depends(require_public_visit_prep_bearer)],
) -> VisitPrepWrongRecipientResponse:
    _invoke(lambda: interview_service.report_wrong_recipient(access_token=access_token))
    return VisitPrepWrongRecipientResponse(disposition="reported")


def visit_prep_public_reject_unsupported_method() -> None:
    """Keep framework-generated 405 responses outside the public surface."""

    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=_UNAVAILABLE_DETAIL,
    )


def visit_prep_public_reject_unsupported_path(unsupported_path: str) -> None:
    """Prevent slash redirects from reflecting bearer-like query strings."""

    del unsupported_path
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=_UNAVAILABLE_DETAIL,
    )


# These routes must be last. Their route class promotes method mismatches from a
# partial to a full match, preventing Starlette's bare 405 response for any HTTP
# extension method while preserving the four explicitly supported operations.
visit_prep_interviews_api_app.add_api_route(
    "",
    visit_prep_public_reject_unsupported_method,
    methods=["GET"],
    include_in_schema=False,
    name="visit_prep_public_reject_unsupported_method",
    route_class_override=_AnyMethodSensitivePublicVisitPrepRoute,
)
visit_prep_interviews_api_app.add_api_route(
    "/{unsupported_path:path}",
    visit_prep_public_reject_unsupported_path,
    methods=["GET"],
    include_in_schema=False,
    name="visit_prep_public_reject_unsupported_path",
    route_class_override=_AnyMethodSensitivePublicVisitPrepRoute,
)
