"""
`fastapi` cli will automatically start a uvicorn server using this file.

Route method names are important as they will be used for the openapi spec which will in turn be used to generate a
JavaScript client which will use these methods.
"""

from typing import Any

import arrow
import orjson
from fastapi import FastAPI, HTTPException, Request
from fastapi.exception_handlers import (
    http_exception_handler,
    request_validation_exception_handler,
)
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette import status

from app.routes.api import external_api_app
from app.routes.errors import EarlyResponseException
from app.routes.recall_callbacks import recall_callbacks_app
from app.routes.utils.openapi import simplify_operation_ids
from app.routes.webhooks import webhook_api_app
from app.routes.zoom_auth import zoom_auth_app

from app.models.doctor import Doctor

from .configuration.public_onboarding import public_onboarding_configured
from .environments import is_production
from .routes.agent import agent_api_app
from .routes.authority_errors import (
    agent_problem_response,
    normalize_trace_id,
    token_problem_response,
)
from .routes.cab_authorization import router as cab_authorization_router
from .routes.chat import chat_api_app
from .routes.companion_appointment_setup import (
    install as install_companion_appointment_setup,
)
from .routes.companion_encounters import install as install_companion_encounters
from .routes.companion_records import install as install_companion_records
from .routes.companion_session_prep import PATH as COMPANION_SESSION_PREP_PATH
from .routes.companion_session_prep import router as companion_session_prep_router
from .routes.embed_context import embed_context_api_app
from .routes.internal import internal_api_app
from .routes.middleware import add_middleware
from .routes.public_onboarding import public_onboarding_api_app
from .routes.scribe_capture_deadline import install_capture_boundaries
from .routes.scribe_encounters import install as install_scribe_encounters
from .routes.scribe_native_capture import native_capture_api_app
from .routes.service import service_api_app
from .routes.static import mount_public_directory

fast_api_args = {}

# disable API documentation in production
if is_production():
    fast_api_args = {
        "docs_url": None,
        "redoc_url": None,
        "openapi_url": None,
    }


class ORJSONSortedResponse(JSONResponse):
    """
    Lifted from the non-sorted fastapi-built version. ORJSONResponse is much faster than JSONResponse, but it does
    not respect the order of the keys:

    https://stackoverflow.com/questions/64408092/how-to-set-response-class-in-fastapi
    """

    def render(self, content: Any) -> bytes:
        return orjson.dumps(content, option=orjson.OPT_NON_STR_KEYS)


# TODO should we set a debug flag? https://fastapi.tiangolo.com/reference/fastapi/?h=debug#fastapi.FastAPI--example
# TODO set `version:` as GIT sha? Set `title:`? https://github.com/fastapiutils/fastapi-utils/blob/e9e7e2c834d703503a3bf5d5605db6232dd853b9/fastapi_utils/api_settings.py#L43


# TODO not possible to type this properly :/ https://github.com/python/typing/discussions/1501
# NOTE `api_app` and not `app` is used intentionally here to make imports more specific
api_app = FastAPI(
    **fast_api_args,  # type: ignore
    default_response_class=ORJSONSortedResponse,
)

api_app.include_router(internal_api_app)
api_app.include_router(cab_authorization_router, include_in_schema=False)
# Workload-only routes intentionally stay out of the browser client schema.
api_app.include_router(service_api_app, include_in_schema=False)
# Agent and context-token contracts are workload/host APIs and intentionally stay
# out of the browser-generated client schema.
api_app.include_router(agent_api_app, include_in_schema=False)
# This composition owns a fresh transaction per existing service phase.
# Do not inherit agent_api_app's request-wide aglobal_session dependency.
api_app.include_router(native_capture_api_app, include_in_schema=False)
api_app.include_router(companion_session_prep_router, include_in_schema=False)
api_app.include_router(embed_context_api_app, include_in_schema=False)
api_app.include_router(external_api_app)
api_app.include_router(chat_api_app)
api_app.include_router(webhook_api_app)
api_app.include_router(zoom_auth_app)
api_app.include_router(recall_callbacks_app)


def register_public_onboarding_routes(app) -> bool:
    """Include the public self-serve lane only when its latch allows it.

    While the latch is false the router is never included, so its paths are
    absent from the application and from the generated OpenAPI schema rather
    than merely refusing at runtime. Kept a function so the latch can be
    exercised against a throwaway app instead of reimporting this module.
    """

    if not public_onboarding_configured():
        return False
    app.include_router(public_onboarding_api_app)
    return True


register_public_onboarding_routes(api_app)

add_middleware(api_app)
# Final-send budgets must wrap the full middleware chain, including no-store,
# logging and session response transforms; route-level sends are intermediate.
install_companion_records(api_app)
install_companion_encounters(api_app)
install_companion_appointment_setup(api_app)
install_scribe_encounters(api_app)
install_capture_boundaries(api_app)

_MOUNT_BOOTSTRAP_PATH = "/service/v1/agent/mount-context/bootstrap"


def _bootstrap_error_trace_id(request: Request) -> str | None:
    """Preserve only one contract-valid bootstrap trace across framework errors."""

    if request.url.path != _MOUNT_BOOTSTRAP_PATH:
        return None
    submitted = request.headers.getlist("x-trace-id")
    return normalize_trace_id(submitted[0] if len(submitted) == 1 else None)


@api_app.exception_handler(HTTPException)
async def safe_authority_http_exception_handler(request: Request, exc: HTTPException):
    if request.url.path.startswith("/embed/v1/context-tokens/"):
        if exc.status_code == status.HTTP_426_UPGRADE_REQUIRED:
            return token_problem_response(
                status_code=exc.status_code,
                code="EMBED_VERSION_UNSUPPORTED",
                title="Embed version unsupported",
                detail="The requested embed contract version is not supported.",
            )
        if exc.status_code == status.HTTP_401_UNAUTHORIZED:
            return token_problem_response(
                status_code=exc.status_code,
                code="SESSION_REQUIRED",
                title="Session required",
                detail="A verified platform session is required.",
            )
        if exc.status_code == status.HTTP_403_FORBIDDEN:
            return token_problem_response(
                status_code=exc.status_code,
                code="REQUEST_DENIED",
                title="Request denied",
                detail="The requested context cannot be issued.",
            )
        return token_problem_response(
            status_code=exc.status_code,
            code="SERVICE_UNAVAILABLE",
            title="Context service unavailable",
            detail="The context-token service is temporarily unavailable.",
            retryable=exc.status_code >= 500,
        )

    if (
        request.url.path.startswith("/service/v1/agent/")
        or request.url.path == COMPANION_SESSION_PREP_PATH
    ):
        trace_id = _bootstrap_error_trace_id(request)
        if exc.status_code == status.HTTP_401_UNAUTHORIZED:
            return agent_problem_response(
                status_code=exc.status_code,
                code="INVALID_WORKLOAD_TOKEN",
                title="Workload authentication failed",
                detail="A valid workload credential is required.",
                trace_id=trace_id,
            )
        if exc.status_code == status.HTTP_403_FORBIDDEN:
            return agent_problem_response(
                status_code=exc.status_code,
                code="WORKLOAD_NOT_ALLOWED",
                title="Workload not allowed",
                detail="The workload is not authorized for this API.",
                trace_id=trace_id,
            )
        return agent_problem_response(
            status_code=exc.status_code,
            code="PLATFORM_UNAVAILABLE",
            title="Platform unavailable",
            detail="The platform agent API is temporarily unavailable.",
            retryable=exc.status_code >= 500,
            trace_id=trace_id,
        )

    return await http_exception_handler(request, exc)


@api_app.exception_handler(RequestValidationError)
async def safe_request_validation_handler(
    request: Request, exc: RequestValidationError
):
    if request.url.path.startswith("/embed/v1/context-tokens/"):
        return token_problem_response(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            code="REQUEST_DENIED",
            title="Invalid request",
            detail="The context-token request is invalid.",
        )
    if (
        request.url.path.startswith("/service/v1/agent/")
        or request.url.path == COMPANION_SESSION_PREP_PATH
    ):
        errors = exc.errors()
        missing_fields = {
            str(error["loc"][-1])
            for error in errors
            if error.get("type") == "missing" and error.get("loc")
        }
        if "X-Cosmaneura-Mount-Context" in missing_fields:
            code = "CONTEXT_REQUIRED"
            status_code = status.HTTP_400_BAD_REQUEST
        elif request.url.path == _MOUNT_BOOTSTRAP_PATH:
            # Bootstrap has no 422 outcome: any malformed declared input is a
            # generic, value-free bad request under Agent API 1.1.
            code = "VALIDATION_FAILED"
            status_code = status.HTTP_400_BAD_REQUEST
        elif "X-Cosmaneura-Interaction-Ref" in missing_fields:
            code = "INTERACTION_REQUIRED"
            status_code = status.HTTP_400_BAD_REQUEST
        elif "X-Cosmaneura-Client-Turn-ID" in missing_fields:
            code = "CLIENT_TURN_REQUIRED"
            status_code = status.HTTP_400_BAD_REQUEST
        elif any(
            error.get("loc") and str(error["loc"][-1]) == "X-Cosmaneura-Interaction-Ref"
            for error in errors
        ):
            code = "INTERACTION_INVALID"
            status_code = status.HTTP_400_BAD_REQUEST
        elif any(
            error.get("loc") and str(error["loc"][-1]) == "X-Cosmaneura-Client-Turn-ID"
            for error in errors
        ):
            code = "CLIENT_TURN_INVALID"
            status_code = status.HTTP_400_BAD_REQUEST
        else:
            code = "VALIDATION_FAILED"
            status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
        return agent_problem_response(
            status_code=status_code,
            code=code,
            title="Invalid request",
            detail="The platform agent API request is invalid.",
            trace_id=_bootstrap_error_trace_id(request),
        )
    if request.url.path.startswith("/service/v1/"):
        # FastAPI's default 422 body includes rejected input. Service payloads
        # can contain PHI, so return only a generic contract error here.
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={"detail": "Invalid service request."},
        )
    return await request_validation_exception_handler(request, exc)


@api_app.get("/healthcheck")
async def healthcheck():
    return {"status": "ok"}


@api_app.get("/status")
async def active_user_status():
    "check if users have logged in within the last day"

    last_24_hours = arrow.utcnow().shift(days=-1).datetime  # noqa: F821
    active_users = Doctor.where(Doctor.last_active_at > last_24_hours).count()  # type: ignore

    if active_users == 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )

    return {"status": "ok"}


@api_app.exception_handler(EarlyResponseException)
async def early_response_handler(request: Request, exc: EarlyResponseException):
    return JSONResponse(status_code=exc.status, content=exc.data)


# important that this is done after all routes are added
simplify_operation_ids(api_app)

# NOTE VERY IMPORTANT that this is done after all routes are added!!
mount_public_directory(api_app)
