from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field

import app.services.authority
from app.configuration.authority import (
    AUTHORITY_KERNEL_SETTINGS,
    AuthorityKernelSettings,
)
from app.configuration.clerk import CLERK_PRIVATE_KEY
from app.services.superadmin_capability import (
    SuperadminCapabilityError,
    configured_superadmin_capability_environment,
)
from app.services.view_as import (
    ViewAsError,
    context_token_exchange_blocked_by_view_as,
    context_token_rotation_blocked_by_view_as,
)

from activemodel.session_manager import aglobal_session, get_session
from sqlalchemy.exc import SQLAlchemyError

from .authority_errors import (
    EMBED_CONTRACT_VERSION,
    token_problem_response,
)
from .dependencies.clerk import AuthenticateClerkRequest
from .dependencies.view_as import view_as_browser_session_present

embed_context_api_app = APIRouter(
    prefix="/embed/v1/context-tokens",
    tags=["embed-context"],
    dependencies=[Depends(aglobal_session)],
)

_embed_bearer = HTTPBearer(auto_error=False)
_authenticate_clerk_session = AuthenticateClerkRequest(CLERK_PRIVATE_KEY)


class AnonymousTokenIssueRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requested_surface: Literal["public"] | None = None


class TokenIssueRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requested_surface: Literal["public", "provider_assistant"] | None = None
    record_ref: str | None = Field(
        default=None,
        min_length=8,
        max_length=160,
        pattern=r"^[A-Za-z0-9._~-]+$",
    )


class ContextTokenResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    context_token: str
    token_type: Literal["Bearer"] = "Bearer"
    issued_at: datetime
    refresh_after: datetime
    expires_at: datetime


@dataclass(frozen=True)
class VerifiedClerkIdentity:
    issuer: str
    subject: str
    clerk_session_id: str


def get_authority_kernel_settings() -> AuthorityKernelSettings:
    return AUTHORITY_KERNEL_SETTINGS


def require_embed_version(
    version: Annotated[
        str | None,
        Header(alias="X-Cosmaneura-Embed-Version"),
    ] = None,
) -> str:
    if version != EMBED_CONTRACT_VERSION:
        raise HTTPException(
            status_code=status.HTTP_426_UPGRADE_REQUIRED,
            detail="The requested embed contract version is not supported.",
        )
    return version


def authenticate_embed_platform_session(
    request: Request,
    credentials: Annotated[
        HTTPAuthorizationCredentials | None,
        Depends(_embed_bearer),
    ],
) -> VerifiedClerkIdentity:
    try:
        auth_state = _authenticate_clerk_session(request, credentials)
    except HTTPException:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="A verified platform session is required.",
        ) from None
    subject = auth_state.payload.get("sub") if auth_state.payload else None
    issuer = auth_state.payload.get("iss") if auth_state.payload else None
    clerk_session_id = auth_state.payload.get("sid") if auth_state.payload else None
    if (
        not isinstance(subject, str)
        or not subject
        or not isinstance(issuer, str)
        or not issuer
        or not isinstance(clerk_session_id, str)
        or not clerk_session_id
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="A verified platform session is required.",
        )
    return VerifiedClerkIdentity(
        issuer=issuer,
        subject=subject,
        clerk_session_id=clerk_session_id,
    )


def _set_token_response_headers(response: Response) -> None:
    response.headers["X-Cosmaneura-Embed-Version"] = EMBED_CONTRACT_VERSION
    response.headers["Cache-Control"] = "no-store"


def _token_response(
    result: app.services.authority.ContextTokenIssueResult,
) -> ContextTokenResponse:
    return ContextTokenResponse(
        context_token=result.context_token,
        issued_at=result.issued_at,
        refresh_after=result.refresh_after,
        expires_at=result.expires_at,
    )


@embed_context_api_app.post(
    "/anonymous",
    response_model=ContextTokenResponse,
    status_code=status.HTTP_201_CREATED,
)
def issue_anonymous_context_token(
    data: AnonymousTokenIssueRequest,
    version: Annotated[str, Depends(require_embed_version)],
) -> JSONResponse:
    del data, version
    return token_problem_response(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        code="SERVICE_UNAVAILABLE",
        title="Anonymous issuance unavailable",
        detail="Anonymous context-token issuance is not available.",
        retryable=False,
    )


@embed_context_api_app.post(
    "/exchange",
    response_model=ContextTokenResponse,
    status_code=status.HTTP_201_CREATED,
)
def exchange_context_token(
    data: TokenIssueRequest,
    request: Request,
    response: Response,
    clerk_identity: Annotated[
        VerifiedClerkIdentity,
        Depends(authenticate_embed_platform_session),
    ],
    settings: Annotated[
        AuthorityKernelSettings, Depends(get_authority_kernel_settings)
    ],
    version: Annotated[str, Depends(require_embed_version)],
) -> ContextTokenResponse | JSONResponse:
    del version
    if view_as_browser_session_present(request):
        return token_problem_response(
            status_code=status.HTTP_403_FORBIDDEN,
            code="REQUEST_DENIED",
            title="Request denied",
            detail="Provider context is unavailable during view-as.",
            retryable=False,
        )
    try:
        if settings.enabled:
            environment = configured_superadmin_capability_environment()
            with get_session() as session:
                if context_token_exchange_blocked_by_view_as(
                    session,
                    issuer=clerk_identity.issuer,
                    subject=clerk_identity.subject,
                    clerk_session_id=clerk_identity.clerk_session_id,
                    environment=environment,
                ):
                    return token_problem_response(
                        status_code=status.HTTP_403_FORBIDDEN,
                        code="REQUEST_DENIED",
                        title="Request denied",
                        detail="Provider context is unavailable during view-as.",
                        retryable=False,
                    )
        result = app.services.authority.exchange_context_token(
            clerk_issuer=clerk_identity.issuer,
            clerk_subject=clerk_identity.subject,
            requested_surface=data.requested_surface,
            record_ref=data.record_ref,
            settings=settings,
        )
    except (SuperadminCapabilityError, ViewAsError):
        return token_problem_response(
            status_code=status.HTTP_403_FORBIDDEN,
            code="REQUEST_DENIED",
            title="Request denied",
            detail="The requested context cannot be issued.",
            retryable=False,
        )
    except app.services.authority.AuthorityKernelDisabledError:
        return token_problem_response(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="SERVICE_UNAVAILABLE",
            title="Context service unavailable",
            detail="Context-token exchange is not available.",
        )
    except app.services.authority.AuthorityRequestDeniedError:
        return token_problem_response(
            status_code=status.HTTP_403_FORBIDDEN,
            code="REQUEST_DENIED",
            title="Request denied",
            detail="The requested context cannot be issued.",
        )
    except SQLAlchemyError:
        return token_problem_response(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="SERVICE_UNAVAILABLE",
            title="Context service unavailable",
            detail="Context-token exchange is temporarily unavailable.",
            retryable=True,
        )

    _set_token_response_headers(response)
    return _token_response(result)


@embed_context_api_app.post(
    "/rotate",
    response_model=ContextTokenResponse,
    status_code=status.HTTP_201_CREATED,
)
def rotate_context_token(
    data: TokenIssueRequest,
    request: Request,
    response: Response,
    credentials: Annotated[
        HTTPAuthorizationCredentials | None,
        Depends(_embed_bearer),
    ],
    settings: Annotated[
        AuthorityKernelSettings, Depends(get_authority_kernel_settings)
    ],
    version: Annotated[str, Depends(require_embed_version)],
) -> ContextTokenResponse | JSONResponse:
    del version
    if view_as_browser_session_present(request):
        return token_problem_response(
            status_code=status.HTTP_403_FORBIDDEN,
            code="REQUEST_DENIED",
            title="Request denied",
            detail="Provider context is unavailable during view-as.",
            retryable=False,
        )
    if credentials is None or credentials.scheme.lower() != "bearer":
        return token_problem_response(
            status_code=status.HTTP_401_UNAUTHORIZED,
            code="TOKEN_INVALID",
            title="Token invalid",
            detail="A valid current context token is required.",
        )

    try:
        if settings.enabled:
            environment = configured_superadmin_capability_environment()
            with get_session() as session:
                if context_token_rotation_blocked_by_view_as(
                    session,
                    current_token=credentials.credentials,
                    environment=environment,
                ):
                    return token_problem_response(
                        status_code=status.HTTP_403_FORBIDDEN,
                        code="REQUEST_DENIED",
                        title="Request denied",
                        detail="Provider context is unavailable during view-as.",
                        retryable=False,
                    )
        result = app.services.authority.rotate_context_token(
            current_token=credentials.credentials,
            requested_surface=data.requested_surface,
            record_ref=data.record_ref,
            settings=settings,
        )
    except (SuperadminCapabilityError, ViewAsError):
        return token_problem_response(
            status_code=status.HTTP_403_FORBIDDEN,
            code="REQUEST_DENIED",
            title="Request denied",
            detail="The requested context cannot be rotated.",
            retryable=False,
        )
    except app.services.authority.AuthorityKernelDisabledError:
        return token_problem_response(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="SERVICE_UNAVAILABLE",
            title="Context service unavailable",
            detail="Context-token rotation is not available.",
        )
    except app.services.authority.ContextTokenExpiredError:
        return token_problem_response(
            status_code=status.HTTP_401_UNAUTHORIZED,
            code="TOKEN_EXPIRED",
            title="Token expired",
            detail="The current context token has expired.",
        )
    except app.services.authority.ContextTokenInvalidError:
        return token_problem_response(
            status_code=status.HTTP_401_UNAUTHORIZED,
            code="TOKEN_INVALID",
            title="Token invalid",
            detail="A valid current context token is required.",
        )
    except app.services.authority.AuthorityRequestDeniedError:
        return token_problem_response(
            status_code=status.HTTP_403_FORBIDDEN,
            code="REQUEST_DENIED",
            title="Request denied",
            detail="The requested context cannot be rotated.",
        )
    except SQLAlchemyError:
        return token_problem_response(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="SERVICE_UNAVAILABLE",
            title="Context service unavailable",
            detail="Context-token rotation is temporarily unavailable.",
            retryable=True,
        )

    _set_token_response_headers(response)
    return _token_response(result)
