"""Unauthenticated-by-account, bearer-authorized Visit Prep interview API."""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, Coroutine, Mapping
from dataclasses import replace
from datetime import datetime
from typing import Annotated, Any, Literal, TypeVar, cast

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
from pydantic import AwareDatetime, BaseModel, BeforeValidator, ConfigDict, Field
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse
from starlette.routing import Match
from starlette.types import Receive, Scope, Send

from app import log
from app.routes.dependencies.view_as import deny_view_as_browser_activity
from app.services import visit_prep_clarification_decisions as clarification_decisions
from app.services import visit_prep_clarification_turns as clarification_turns
from app.services import visit_prep_contributions as contributions
from app.services import visit_prep_email_consent as email_consent
from app.services import visit_prep_interviews as interview_service
from app.services import visit_prep_patient_analysis as patient_analysis
from app.services import visit_prep_rate_limits as rate_limits
from app.services.visit_prep_clarification_contract import (
    PatientClarificationDecision,
    VisitPrepClarificationChanged,
    VisitPrepClarificationInvalid,
)
from app.services.visit_prep_clarification_view import (
    get_patient_clarification_view,
    get_patient_followup_view,
)
from app.services.visit_prep_contribution_contract import (
    ContributionBasis,
    ContributionMutationReceipt,
    PatientContribution,
)
from app.services.visit_prep_email_consent_view import get_patient_email_consent_view
from app.services.visit_prep_email_contract import VisitPrepEmailInvalid
from app.services.visit_prep_interview_contract import (
    InterviewMutationResult,
    InterviewQuestion,
    InterviewState,
    ResponseInput,
    VisitPrepInterviewConflict,
    VisitPrepInterviewUnavailable,
    VisitPrepInterviewValidation,
)
from app.services.visit_prep_patient_continuation import (
    COOKIE_NAME,
    REQUEST_HEADER,
    ConfirmedVisitPrepAccess,
    PatientContinuationInvalid,
    open_confirmation,
    seal_confirmation,
)
from app.services.visit_preps import access_token_hash

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
_CONFIRMATION_PATHS = frozenset(
    f"{_PUBLIC_PREFIX}{suffix}"
    for suffix in (
        "/confirm",
        "/wrong-recipient",
        "/contribution",
        "/contribution/response",
        "/contribution/explanation",
        "/contribution/review",
        "/clarification",
        "/clarification/decision",
        "/clarification/follow-up",
        "/clarification/turn",
        "/clarification/generate",
        "/email-consent",
        "/email-consent/decision",
    )
)

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
                status_code = (
                    404
                    if exc.status_code == 403
                    else (exc.status_code if exc.status_code in details else 503)
                )
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
    continued_role: Literal["patient", "caregiver"] | None = None


class VisitPrepPublicMutationResponse(_StrictModel):
    interview: VisitPrepPublicStateResponse
    disposition: Literal["confirmed", "accepted", "replayed", "completed"]


class VisitPrepWrongRecipientResponse(_StrictModel):
    disposition: Literal["reported"]


class VisitPrepConfirmationRequest(_StrictModel):
    first_name: str | None = Field(
        default=None, min_length=1, max_length=120, repr=False
    )
    role: Literal["patient", "caregiver"]


class VisitPrepInitialConfirmationRequest(_StrictModel):
    first_name: str = Field(min_length=1, max_length=120, repr=False)
    role: Literal["patient", "caregiver"]


def _explicit_consent_timestamp(value: object) -> object:
    if value is not None and (
        type(value) is not str or len(value) > 64 or value[10:11] not in {"T", "t"}
    ):
        raise ValueError("an explicit timezone-aware timestamp is required")
    return value


class VisitPrepPatientEmailConsentResponse(_StrictModel):
    contract: Literal["visit_prep.patient_email_consent.v1"] = (
        "visit_prep.patient_email_consent.v1"
    )
    actor_role: Literal["patient", "caregiver"]
    disclosure_version: str
    grant_disclosure: str
    grant_disclosure_fingerprint: str
    withdrawal_disclosure: str
    withdrawal_disclosure_fingerprint: str
    status: Literal["not_recorded", "grant_recorded", "withdrawn", "expired"]
    event_sequence: int
    recorded_at: datetime | None
    expires_at: datetime | None
    maximum_expires_at: datetime
    can_grant: bool
    checked_at: datetime
    identity_assurance: Literal["invitation_bearer"] = "invitation_bearer"
    sending_authority: Literal["not_qualified"] = "not_qualified"
    delivery_status: Literal["not_connected"] = "not_connected"


class VisitPrepPatientEmailConsentDecisionRequest(VisitPrepConfirmationRequest):
    expected_event_sequence: int = Field(ge=0, lt=2_147_483_647)
    event_kind: Literal["granted", "withdrawn"]
    disclosure_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    expires_at: Annotated[
        AwareDatetime | None,
        Field(strict=False),
        BeforeValidator(_explicit_consent_timestamp),
    ]
    recipient_control_attested: bool
    caregiver_authority_attested: bool


class VisitPrepPatientEmailConsentDecisionResponse(_StrictModel):
    event_sequence: int
    current_event_sequence: int
    event_kind: Literal["granted", "withdrawn"]
    expires_at: datetime | None
    recorded_at: datetime
    disposition: Literal["recorded", "replayed"]


class VisitPrepResponseRequest(_StrictModel):
    question_key: str = Field(min_length=1, max_length=64)
    response_kind: Literal["answered", "skipped"]
    answer: str | None = Field(default=None, repr=False)


class VisitPrepContributionBasis(_StrictModel):
    original_response_count: int = Field(ge=0, le=5)
    event_sequence: int = Field(ge=0)


class VisitPrepContributionChangeRequest(VisitPrepConfirmationRequest):
    basis: VisitPrepContributionBasis
    question_key: str = Field(min_length=1, max_length=64)
    response_kind: Literal["answered", "skipped"]
    answer: str | None = Field(default=None, max_length=2000, repr=False)


class VisitPrepContributionReviewRequest(VisitPrepConfirmationRequest):
    basis: VisitPrepContributionBasis


class VisitPrepExplanationRequest(VisitPrepContributionReviewRequest):
    question_key: str = Field(min_length=1, max_length=64)
    patient_words: str | None = Field(max_length=2000, repr=False)


class VisitPrepPatientReviewTopic(_StrictModel):
    question: VisitPrepPublicQuestionResponse
    response_kind: Literal["answered", "skipped"] | None
    answer: str | None = Field(repr=False)
    initial_response_kind: Literal["answered", "skipped"] | None
    initial_answer: str | None = Field(repr=False)
    changed: bool
    recorded_at: datetime | None

    explanation_words: str | None = Field(default=None, repr=False)
    explanation_recorded_at: datetime | None = None


class VisitPrepPatientContributionResponse(_StrictModel):
    basis: VisitPrepContributionBasis
    actor_role: Literal["patient", "caregiver"]
    original_interview_completed: bool
    current_answers_complete: bool
    current_answers_reviewed: bool
    topics: list[VisitPrepPatientReviewTopic] = Field(repr=False)


class VisitPrepContributionReceiptResponse(_StrictModel):
    disposition: Literal["accepted", "replayed"]
    basis: VisitPrepContributionBasis
    current_answers_complete: bool
    current_answers_reviewed: bool


class VisitPrepClarificationReadRequest(VisitPrepConfirmationRequest):
    question_key: str = Field(min_length=1, max_length=64)


class VisitPrepClarificationTurnResponse(_StrictModel):
    prompt: str = Field(max_length=200, repr=False)
    patient_words: str = Field(min_length=1, max_length=2000, repr=False)


class VisitPrepClarificationViewResponse(_StrictModel):
    contract: Literal["visit_prep.patient_clarification.v1"] = (
        "visit_prep.patient_clarification.v1"
    )
    status: Literal["available", "not_available"]
    question_key: str
    actor_role: Literal["patient", "caregiver"]
    basis: VisitPrepContributionBasis
    proposal_id: str | None
    current_words: str | None = Field(max_length=2000, repr=False)
    organized_words: str | None = Field(max_length=2000, repr=False)
    turns: list[VisitPrepClarificationTurnResponse] = Field(max_length=2, repr=False)


class VisitPrepClarificationDecisionRequest(VisitPrepConfirmationRequest):
    proposal_id: str = Field(min_length=1, max_length=64)
    action: Literal["confirm", "edit", "keep_original", "dismiss"]
    edited_words: str | None = Field(
        default=None, min_length=1, max_length=2000, repr=False
    )


class VisitPrepFollowupViewResponse(_StrictModel):
    contract: Literal["visit_prep.patient_followup.v1"] = (
        "visit_prep.patient_followup.v1"
    )
    status: Literal["prompt", "answered", "not_available"]
    question_key: str
    actor_role: Literal["patient", "caregiver"]
    basis: VisitPrepContributionBasis
    proposal_id: str | None
    current_words: str | None = Field(max_length=2000, repr=False)
    pending_prompt: str | None = Field(max_length=200, repr=False)
    source_turn_id: str | None = None
    turns: list[VisitPrepClarificationTurnResponse] = Field(max_length=2, repr=False)


class VisitPrepClarificationTurnRequest(VisitPrepConfirmationRequest):
    proposal_id: str = Field(min_length=1, max_length=64)
    patient_words: str = Field(min_length=1, max_length=2000, repr=False)


class VisitPrepPatientTurnReceiptResponse(_StrictModel):
    turn_id: str
    proposal_id: str
    disposition: Literal["accepted", "replayed"]
    turn_sequence: Literal[1, 2]
    basis: VisitPrepContributionBasis


class VisitPrepClarificationReceiptResponse(VisitPrepContributionReceiptResponse):
    proposal_id: str
    decision_id: str
    action: Literal["confirm", "edit", "keep_original", "dismiss"]


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
) -> str | ConfirmedVisitPrepAccess:
    """Rate-limit and validate a bearer or an opt-in sealed confirmation."""

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
    continued = None
    continuation_headers = request.headers.getlist(REQUEST_HEADER)
    if continuation_headers and continuation_headers != ["1"]:
        raise HTTPException(status_code=404, detail=_UNAVAILABLE_DETAIL)
    continuation_requested = continuation_headers == ["1"]
    if continuation_requested:
        if request.headers.getlist("authorization") and access_token is None:
            raise HTTPException(status_code=404, detail=_UNAVAILABLE_DETAIL)
        origins = request.headers.getlist("origin")
        allowed = getattr(request.app.state, "visit_prep_allowed_origins", frozenset())
        if (
            len(origins) > 1
            or (
                origins
                and origins[0] not in allowed
                and origins[0] != str(request.base_url).rstrip("/")
            )
            or request.headers.get("sec-fetch-site") == "cross-site"
        ):
            raise HTTPException(status_code=404, detail=_UNAVAILABLE_DETAIL)
        # Non-simple header plus strict Origin/CORS prevents cross-site cookie use.
        cookie_count = sum(
            part.strip().split("=", 1)[0] == COOKIE_NAME
            for header in request.headers.getlist("cookie")
            for part in header.split(";")
        )
        if cookie_count > 1:
            raise HTTPException(status_code=404, detail=_UNAVAILABLE_DETAIL)
        cookie = request.cookies.get(COOKIE_NAME)
        if cookie:
            try:
                candidate = open_confirmation(cookie)
                if access_token is None:
                    continued = candidate
                else:
                    presented = access_token_hash(access_token)
                    if candidate.token_hash == presented:
                        continued = candidate
                    elif interview_service.confirmation_matches_invitation(
                        access=candidate, presented_token_hash=presented
                    ):
                        continued = replace(candidate, presented_token_hash=presented)
            except (PatientContinuationInvalid, ValueError):
                pass
    if continued is not None and request.url.path != f"{_PUBLIC_PREFIX}/confirm":
        try:
            rate_limits.enforce_credential_rate_limits(
                network_signal=network_signal,
                access_token=continued,
                confirmation=False,
            )
        except (rate_limits.RateLimited, rate_limits.RateLimitUnavailable) as exc:
            _record_rate_limit_failure(exc, stage="credential")
            raise _rate_limit_error(exc) from None
        return continued
    if access_token is None:
        try:
            rate_limits.enforce_credential_rate_limits(
                network_signal=network_signal,
                access_token=None,
                confirmation=request.url.path in _CONFIRMATION_PATHS,
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
            confirmation=request.url.path in _CONFIRMATION_PATHS,
        )
    except (
        rate_limits.RateLimited,
        rate_limits.RateLimitUnavailable,
    ) as exc:
        _record_rate_limit_failure(exc, stage="credential")
        raise _rate_limit_error(exc) from None
    if (
        continuation_requested
        and request.method != "GET"
        and request.url.path
        not in {f"{_PUBLIC_PREFIX}/confirm", f"{_PUBLIC_PREFIX}/wrong-recipient"}
    ):
        raise HTTPException(status_code=404, detail=_UNAVAILABLE_DETAIL)
    return access_token


visit_prep_interviews_api_app = APIRouter(
    prefix="/external/v1/visit-prep",
    tags=["visit-prep-public"],
    dependencies=[Depends(deny_view_as_browser_activity), Depends(aglobal_session)],
    route_class=_SensitivePublicVisitPrepRoute,
    responses=_ERROR_RESPONSES,
)


def _invoke(operation: Callable[[], _ResultT]) -> _ResultT:
    try:
        return operation()
    except (VisitPrepInterviewUnavailable, VisitPrepClarificationInvalid):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_UNAVAILABLE_DETAIL,
        ) from None
    except VisitPrepInterviewValidation:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=_VALIDATION_DETAIL,
        ) from None
    except (VisitPrepInterviewConflict, VisitPrepClarificationChanged):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=_CONFLICT_DETAIL,
        ) from None
    except PatientContinuationInvalid:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=_TEMPORARY_DETAIL,
        ) from None
    except patient_analysis.PatientAnalysisUnavailable:
        raise HTTPException(status_code=503, detail=_TEMPORARY_DETAIL) from None
    except SQLAlchemyError:
        log.warning(
            "Visit Prep public persistence unavailable",
            action="visit_prep.public_persistence_unavailable",
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=_TEMPORARY_DETAIL,
        ) from None


def _question_response(question: InterviewQuestion) -> VisitPrepPublicQuestionResponse:
    return VisitPrepPublicQuestionResponse(
        key=question.key,
        position=question.position,
        prompt=question.prompt,
        helper_text=question.helper_text,
        kind=question.kind,
        choices=[
            VisitPrepPublicChoiceResponse(
                value=choice.value, label=choice.label, hint=choice.hint
            )
            for choice in question.choices
        ],
    )


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
            _question_response(question) if question is not None else None
        ),
        continued_role=state.continued_role,
    )


def _mutation_response(
    result: InterviewMutationResult,
) -> VisitPrepPublicMutationResponse:
    return VisitPrepPublicMutationResponse(
        interview=_state_response(result.interview),
        disposition=result.disposition,
    )


def _recipient_name(
    data: VisitPrepConfirmationRequest,
    access: str | ConfirmedVisitPrepAccess,
) -> str:
    if data.first_name is None:
        if (
            isinstance(access, ConfirmedVisitPrepAccess)
            and access.actor_role == data.role
        ):
            return ""
        raise HTTPException(status_code=404, detail=_UNAVAILABLE_DETAIL)
    return data.first_name


@visit_prep_interviews_api_app.get(
    "",
    name="visit_prep_public_resume",
    response_model=VisitPrepPublicStateResponse,
)
def visit_prep_public_resume(
    request: Request,
    response: Response,
    access_token: Annotated[
        str | ConfirmedVisitPrepAccess, Depends(require_public_visit_prep_bearer)
    ],
) -> VisitPrepPublicStateResponse:
    if isinstance(access_token, ConfirmedVisitPrepAccess):
        state, continuation = _invoke(
            lambda: interview_service.resume_confirmed_interview(access=access_token)
        )
        _remember_confirmation(response, continuation)
    else:
        state = _invoke(
            lambda: interview_service.get_interview(access_token=access_token)
        )
    if request.headers.getlist(REQUEST_HEADER) == ["1"] and not isinstance(
        access_token, ConfirmedVisitPrepAccess
    ):
        return VisitPrepPublicStateResponse(
            status="confirmation_required",
            physician_display_name=None,
            practice_display_name=None,
            scheduled_at=None,
            timezone_name=None,
            submitted_questions=0,
            maximum_questions=5,
            current_question=None,
        )
    return _state_response(state)


@visit_prep_interviews_api_app.post(
    "/confirm",
    name="visit_prep_public_confirm",
    response_model=VisitPrepPublicMutationResponse,
)
def visit_prep_public_confirm(
    request: Request,
    response: Response,
    data: VisitPrepInitialConfirmationRequest,
    access_token: Annotated[
        str | ConfirmedVisitPrepAccess, Depends(require_public_visit_prep_bearer)
    ],
) -> VisitPrepPublicMutationResponse:
    if not isinstance(access_token, str):
        raise HTTPException(status_code=404, detail=_UNAVAILABLE_DETAIL)
    result = _invoke(
        lambda: interview_service.confirm_interview(
            access_token=access_token,
            first_name=data.first_name,
            role=data.role,
        )
    )
    if request.headers.getlist(REQUEST_HEADER) == ["1"]:
        continuation = result.continuation
        if continuation is None:
            raise HTTPException(status_code=503, detail=_TEMPORARY_DETAIL)
        _remember_confirmation(response, continuation)
    return _mutation_response(result)


def _remember_confirmation(response: Response, continuation: ConfirmedVisitPrepAccess):
    cookie = _invoke(lambda: seal_confirmation(continuation))
    response.set_cookie(
        COOKIE_NAME,
        cookie,
        expires=continuation.expires_at,
        secure=True,
        httponly=True,
        samesite="strict",
        path="/",
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
    access_token: Annotated[
        str | ConfirmedVisitPrepAccess, Depends(require_public_visit_prep_bearer)
    ],
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
    access_token: Annotated[
        str | ConfirmedVisitPrepAccess, Depends(require_public_visit_prep_bearer)
    ],
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
    access_token: Annotated[
        str | ConfirmedVisitPrepAccess, Depends(require_public_visit_prep_bearer)
    ],
) -> VisitPrepWrongRecipientResponse:
    _invoke(lambda: interview_service.report_wrong_recipient(access_token=access_token))
    return VisitPrepWrongRecipientResponse(disposition="reported")


def _contribution_basis(basis: ContributionBasis) -> VisitPrepContributionBasis:
    return VisitPrepContributionBasis(
        original_response_count=basis.original_response_count,
        event_sequence=basis.event_sequence,
    )


def _patient_contribution_response(
    current: PatientContribution,
) -> VisitPrepPatientContributionResponse:
    # Never serialize the internal dataclass: it carries owner/source IDs and
    # retained hidden responses. Only explicitly applicable topics leave Core.
    topics = []
    for topic in current.topics:
        if not topic.applicable:
            continue
        original = topic.original_response_id is not None
        initial_kind = (
            topic.original_response_kind if original else topic.additional_response_kind
        )
        initial_answer = topic.original_answer if original else topic.additional_answer
        topics.append(
            VisitPrepPatientReviewTopic(
                question=_question_response(topic.question),
                response_kind=topic.response_kind,
                answer=topic.answer,
                initial_response_kind=initial_kind,
                initial_answer=initial_answer,
                changed=topic.response_kind != initial_kind
                or topic.answer != initial_answer,
                recorded_at=topic.recorded_at,
                explanation_words=topic.explanation_words,
                explanation_recorded_at=topic.explanation_recorded_at,
            )
        )
    return VisitPrepPatientContributionResponse(
        basis=_contribution_basis(current.basis),
        actor_role=current.actor_role,
        original_interview_completed=current.original_interview_completed,
        current_answers_complete=current.current_answers_complete,
        current_answers_reviewed=current.current_answers_reviewed,
        topics=topics,
    )


def _contribution_receipt(
    receipt: ContributionMutationReceipt,
) -> VisitPrepContributionReceiptResponse:
    return VisitPrepContributionReceiptResponse(
        disposition=receipt.disposition,
        basis=_contribution_basis(receipt.basis),
        current_answers_complete=receipt.current_answers_complete,
        current_answers_reviewed=receipt.current_answers_reviewed,
    )


def _contribution_key(request: Request) -> str:
    values = request.headers.getlist("idempotency-key")
    if len(values) != 1:
        raise HTTPException(status_code=422, detail=_VALIDATION_DETAIL)
    return values[0]


def _email_consent_invoke(operation: Callable[[], _ResultT]) -> _ResultT:
    result = None
    try:
        result = operation()
    except VisitPrepEmailInvalid:
        pass
    if result is None:
        # Never distinguish private authority, persistence, or stale-choice errors.
        # A failed mutation may already have committed. Recovery is a fresh read.
        raise HTTPException(status_code=409, detail=_CONFLICT_DETAIL)
    return result


@visit_prep_interviews_api_app.post(
    "/email-consent",
    name="visit_prep_public_read_email_consent",
    response_model=VisitPrepPatientEmailConsentResponse,
)
def visit_prep_public_read_email_consent(
    data: VisitPrepConfirmationRequest,
    access_token: Annotated[
        str | ConfirmedVisitPrepAccess, Depends(require_public_visit_prep_bearer)
    ],
) -> VisitPrepPatientEmailConsentResponse:
    value = _email_consent_invoke(
        lambda: get_patient_email_consent_view(
            access_token=access_token,
            first_name=_recipient_name(data, access_token),
            role=data.role,
        )
    )
    return VisitPrepPatientEmailConsentResponse(
        actor_role=value.actor_role,
        disclosure_version=value.disclosure_version,
        grant_disclosure=value.grant_disclosure,
        grant_disclosure_fingerprint=value.grant_disclosure_fingerprint,
        withdrawal_disclosure=value.withdrawal_disclosure,
        withdrawal_disclosure_fingerprint=value.withdrawal_disclosure_fingerprint,
        status=value.status,
        event_sequence=value.event_sequence,
        recorded_at=value.recorded_at,
        expires_at=value.expires_at,
        maximum_expires_at=value.maximum_expires_at,
        can_grant=value.can_grant,
        checked_at=value.checked_at,
    )


@visit_prep_interviews_api_app.post(
    "/email-consent/decision",
    name="visit_prep_public_decide_email_consent",
    response_model=VisitPrepPatientEmailConsentDecisionResponse,
)
def visit_prep_public_decide_email_consent(
    request: Request,
    data: VisitPrepPatientEmailConsentDecisionRequest,
    documented_idempotency_key: Annotated[
        str, Header(alias="Idempotency-Key", min_length=36, max_length=36)
    ],
    access_token: Annotated[
        str | ConfirmedVisitPrepAccess, Depends(require_public_visit_prep_bearer)
    ],
) -> VisitPrepPatientEmailConsentDecisionResponse:
    del documented_idempotency_key
    value = _email_consent_invoke(
        lambda: email_consent.record_confirmed_patient_email_consent(
            access_token=access_token,
            first_name=_recipient_name(data, access_token),
            decision=email_consent.PatientEmailConsentInput(
                operation_id=_contribution_key(request),
                expected_event_sequence=data.expected_event_sequence,
                event_kind=data.event_kind,
                role=data.role,
                disclosure_fingerprint=data.disclosure_fingerprint,
                expires_at=data.expires_at,
                recipient_control_attested=data.recipient_control_attested,
                caregiver_authority_attested=data.caregiver_authority_attested,
            ),
        )
    )
    return VisitPrepPatientEmailConsentDecisionResponse(
        event_sequence=value.event_sequence,
        current_event_sequence=value.current_event_sequence,
        event_kind=value.event_kind,
        expires_at=value.expires_at,
        recorded_at=value.recorded_at,
        disposition=value.disposition,
    )


@visit_prep_interviews_api_app.post(
    "/contribution",
    name="visit_prep_public_read_contribution",
    response_model=VisitPrepPatientContributionResponse,
)
def visit_prep_public_read_contribution(
    data: VisitPrepConfirmationRequest,
    access_token: Annotated[
        str | ConfirmedVisitPrepAccess, Depends(require_public_visit_prep_bearer)
    ],
) -> VisitPrepPatientContributionResponse:
    return _patient_contribution_response(
        _invoke(
            lambda: contributions.get_patient_contribution(
                access_token=access_token,
                first_name=_recipient_name(data, access_token),
                role=data.role,
            )
        )
    )


@visit_prep_interviews_api_app.post(
    "/contribution/response",
    name="visit_prep_public_correct_contribution",
    response_model=VisitPrepContributionReceiptResponse,
)
def visit_prep_public_correct_contribution(
    request: Request,
    data: VisitPrepContributionChangeRequest,
    documented_idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
    access_token: Annotated[
        str | ConfirmedVisitPrepAccess, Depends(require_public_visit_prep_bearer)
    ],
) -> VisitPrepContributionReceiptResponse:
    del documented_idempotency_key
    key = _contribution_key(request)
    return _contribution_receipt(
        _invoke(
            lambda: contributions.correct_contribution(
                access_token=access_token,
                first_name=_recipient_name(data, access_token),
                role=data.role,
                expected_basis=ContributionBasis(
                    data.basis.original_response_count, data.basis.event_sequence
                ),
                idempotency_key=key,
                response=ResponseInput(
                    data.question_key, data.response_kind, data.answer
                ),
            )
        )
    )


@visit_prep_interviews_api_app.post(
    "/contribution/explanation",
    name="visit_prep_public_explain_contribution",
    response_model=VisitPrepContributionReceiptResponse,
)
def visit_prep_public_explain_contribution(
    request: Request,
    data: VisitPrepExplanationRequest,
    documented_idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
    access_token: Annotated[
        str | ConfirmedVisitPrepAccess, Depends(require_public_visit_prep_bearer)
    ],
) -> VisitPrepContributionReceiptResponse:
    del documented_idempotency_key
    return _contribution_receipt(
        _invoke(
            lambda: contributions.explain_contribution(
                access_token=access_token,
                first_name=_recipient_name(data, access_token),
                role=data.role,
                expected_basis=ContributionBasis(
                    data.basis.original_response_count, data.basis.event_sequence
                ),
                idempotency_key=_contribution_key(request),
                question_key=data.question_key,
                patient_words=data.patient_words,
            )
        )
    )


@visit_prep_interviews_api_app.post(
    "/contribution/review",
    name="visit_prep_public_review_contribution",
    response_model=VisitPrepContributionReceiptResponse,
)
def visit_prep_public_review_contribution(
    request: Request,
    data: VisitPrepContributionReviewRequest,
    documented_idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
    access_token: Annotated[
        str | ConfirmedVisitPrepAccess, Depends(require_public_visit_prep_bearer)
    ],
) -> VisitPrepContributionReceiptResponse:
    del documented_idempotency_key
    key = _contribution_key(request)
    return _contribution_receipt(
        _invoke(
            lambda: contributions.review_contribution(
                access_token=access_token,
                first_name=_recipient_name(data, access_token),
                role=data.role,
                expected_basis=ContributionBasis(
                    data.basis.original_response_count, data.basis.event_sequence
                ),
                idempotency_key=key,
            )
        )
    )


@visit_prep_interviews_api_app.post(
    "/clarification",
    name="visit_prep_public_read_clarification",
    response_model=VisitPrepClarificationViewResponse,
)
def visit_prep_public_read_clarification(
    data: VisitPrepClarificationReadRequest,
    access_token: Annotated[
        str | ConfirmedVisitPrepAccess, Depends(require_public_visit_prep_bearer)
    ],
) -> VisitPrepClarificationViewResponse:
    view = _invoke(
        lambda: get_patient_clarification_view(
            access_token=access_token,
            first_name=_recipient_name(data, access_token),
            role=data.role,
            question_key=data.question_key,
        )
    )
    return VisitPrepClarificationViewResponse(
        status=cast(Literal["available", "not_available"], view.status),
        question_key=view.question_key,
        actor_role=view.actor_role,
        basis=VisitPrepContributionBasis(
            original_response_count=view.basis.original_response_count,
            event_sequence=view.basis.event_sequence,
        ),
        proposal_id=view.proposal_id,
        current_words=view.current_words,
        organized_words=view.organized_words,
        turns=[
            VisitPrepClarificationTurnResponse(
                prompt=t.prompt, patient_words=t.patient_words
            )
            for t in view.turns
        ],
    )


@visit_prep_interviews_api_app.post(
    "/clarification/follow-up",
    name="visit_prep_public_read_followup",
    response_model=VisitPrepFollowupViewResponse,
)
def visit_prep_public_read_followup(
    data: VisitPrepClarificationReadRequest,
    access_token: Annotated[
        str | ConfirmedVisitPrepAccess, Depends(require_public_visit_prep_bearer)
    ],
) -> VisitPrepFollowupViewResponse:
    view = _invoke(
        lambda: get_patient_followup_view(
            access_token=access_token,
            first_name=_recipient_name(data, access_token),
            role=data.role,
            question_key=data.question_key,
        )
    )
    return VisitPrepFollowupViewResponse(
        status=cast(Literal["prompt", "answered", "not_available"], view.status),
        question_key=view.question_key,
        actor_role=view.actor_role,
        basis=VisitPrepContributionBasis(
            original_response_count=view.basis.original_response_count,
            event_sequence=view.basis.event_sequence,
        ),
        proposal_id=view.proposal_id,
        current_words=view.current_words,
        pending_prompt=view.pending_prompt,
        source_turn_id=view.source_turn_id,
        turns=[
            VisitPrepClarificationTurnResponse(
                prompt=t.prompt, patient_words=t.patient_words
            )
            for t in view.turns
        ],
    )


class VisitPrepClarificationGenerateRequest(VisitPrepClarificationReadRequest):
    basis: VisitPrepContributionBasis
    source_turn_id: str | None = Field(default=None, max_length=100)


class VisitPrepClarificationGenerateReceipt(BaseModel):
    proposal_id: str
    basis: VisitPrepContributionBasis
    disposition: Literal["accepted", "replayed"]


@visit_prep_interviews_api_app.post(
    "/clarification/generate",
    name="visit_prep_public_generate_clarification",
    response_model=VisitPrepClarificationGenerateReceipt,
)
def visit_prep_public_generate_clarification(
    request: Request,
    data: VisitPrepClarificationGenerateRequest,
    documented_idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
    access_token: Annotated[
        str | ConfirmedVisitPrepAccess, Depends(require_public_visit_prep_bearer)
    ],
):
    del documented_idempotency_key
    receipt = _invoke(
        lambda: patient_analysis.request_patient_clarification(
            access_token=access_token,
            first_name=_recipient_name(data, access_token),
            role=data.role,
            question_key=data.question_key,
            expected_basis=ContributionBasis(
                data.basis.original_response_count, data.basis.event_sequence
            ),
            operation_id=_contribution_key(request),
            source_turn_id=data.source_turn_id,
        )
    )
    return VisitPrepClarificationGenerateReceipt(
        proposal_id=receipt.proposal_id,
        basis=VisitPrepContributionBasis(**vars(receipt.basis)),
        disposition=receipt.disposition,
    )


@visit_prep_interviews_api_app.post(
    "/clarification/turn",
    name="visit_prep_public_record_clarification_turn",
    response_model=VisitPrepPatientTurnReceiptResponse,
)
def visit_prep_public_record_clarification_turn(
    request: Request,
    data: VisitPrepClarificationTurnRequest,
    documented_idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
    access_token: Annotated[
        str | ConfirmedVisitPrepAccess, Depends(require_public_visit_prep_bearer)
    ],
) -> VisitPrepPatientTurnReceiptResponse:
    del documented_idempotency_key
    receipt = _invoke(
        lambda: clarification_turns.record_patient_clarification_turn(
            access_token=access_token,
            first_name=_recipient_name(data, access_token),
            role=data.role,
            proposal_id=data.proposal_id,
            operation_id=_contribution_key(request),
            patient_words=data.patient_words,
        )
    )
    return VisitPrepPatientTurnReceiptResponse(
        turn_id=receipt.turn_id,
        proposal_id=receipt.proposal_id,
        disposition=receipt.disposition,
        turn_sequence=cast(Literal[1, 2], receipt.turn_sequence),
        basis=VisitPrepContributionBasis(
            original_response_count=receipt.basis.original_response_count,
            event_sequence=receipt.basis.event_sequence,
        ),
    )


@visit_prep_interviews_api_app.post(
    "/clarification/decision",
    name="visit_prep_public_decide_clarification",
    response_model=VisitPrepClarificationReceiptResponse,
)
def visit_prep_public_decide_clarification(
    request: Request,
    data: VisitPrepClarificationDecisionRequest,
    documented_idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
    access_token: Annotated[
        str | ConfirmedVisitPrepAccess, Depends(require_public_visit_prep_bearer)
    ],
) -> VisitPrepClarificationReceiptResponse:
    del documented_idempotency_key
    receipt = _invoke(
        lambda: clarification_decisions.record_patient_clarification_decision(
            access_token=access_token,
            first_name=_recipient_name(data, access_token),
            role=data.role,
            proposal_id=data.proposal_id,
            operation_id=_contribution_key(request),
            decision=PatientClarificationDecision(data.action, data.edited_words),
        )
    )
    return VisitPrepClarificationReceiptResponse(
        proposal_id=receipt.proposal_id,
        decision_id=receipt.decision_id,
        action=receipt.action,
        disposition=receipt.disposition,
        basis=VisitPrepContributionBasis(
            original_response_count=receipt.basis.original_response_count,
            event_sequence=receipt.basis.event_sequence,
        ),
        current_answers_complete=receipt.current_answers_complete,
        current_answers_reviewed=receipt.current_answers_reviewed,
    )


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
# extension method while preserving the explicitly supported operations.
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
