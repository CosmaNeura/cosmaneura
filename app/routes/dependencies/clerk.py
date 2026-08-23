"""
Attempting to upstream at: https://github.com/clerk/clerk-sdk-python/pull/65/files
"""

import httpx
from clerk_backend_api import Clerk
from clerk_backend_api.jwks_helpers.authenticaterequest import (
    AuthenticateRequestOptions,
    AuthStatus,
    RequestState,
)
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app import log
from app.environments import is_testing

# Bearer tokens with this prefix are accepted by AuthenticateClerkRequest
# only when PYTHON_ENV=test. The Clerk id encoded after the prefix must
# itself start with "user_test_" so prod traffic (where is_testing() is
# False) can never be impersonated even if a header leaked.
_TEST_BEARER_PREFIX = "test:"
_TEST_CLERK_ID_PREFIX = "user_test_"
TEST_CLERK_ISSUER = "https://test.clerk.invalid"


class AuthenticateClerkRequest:
    """
    Protect a route or specific route:

    >>> protected_router = APIRouter(
    >>>    prefix="/protected",
    >>>    dependencies=[Depends(AuthenticateRequest("clerk_secret_key"))],
    >>> )

    Originally sourced from: https://github.com/clerk/clerk-sdk-python/issues/49
    """

    def __init__(self, clerk_secret_key: str):
        self.clerk_secret_key = clerk_secret_key
        # self.sdk.sdk_configuration contains most of the core configuration
        self.sdk = Clerk(bearer_auth=clerk_secret_key)

    # NOTE: declared `def`, not `async def`. The Clerk SDK's
    # `authenticate_request` is synchronous, and on JWKS cache miss it
    # blocks for the duration of an HTTPS round-trip to
    # api.clerk.com/v1/jwks (~100-300ms). If this method were async,
    # that sync call would block the event loop and serialize every
    # concurrent request on the worker. FastAPI runs sync dependencies
    # in a threadpool automatically, so the event loop stays free.
    def __call__(
        self,
        request: Request,
        credentials: HTTPAuthorizationCredentials | None = Depends(HTTPBearer()),
    ) -> RequestState:
        if not credentials:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Not Authenticated"
            )

        # Test-only bypass. The frontend's auth fn returns a bearer
        # of the form "test:user_test_<id>" when running under the
        # Playwright integration tests. We honor it ONLY in PYTHON_ENV=test;
        # prod and staging fall through to real Clerk verification.
        token = credentials.credentials
        if (
            is_testing()
            and token.startswith(_TEST_BEARER_PREFIX)
            and token[len(_TEST_BEARER_PREFIX) :].startswith(_TEST_CLERK_ID_PREFIX)
        ):
            test_clerk_id = token[len(_TEST_BEARER_PREFIX) :]
            log.warning("auth bypass active (PYTHON_ENV=test)", clerk_id=test_clerk_id)
            bypass_state = RequestState(
                status=AuthStatus.SIGNED_IN,
                payload={"iss": TEST_CLERK_ISSUER, "sub": test_clerk_id},
            )
            request.state.auth_state = bypass_state
            return bypass_state

        # Convert FastAPI request headers to httpx format
        httpx_request = httpx.Request(
            method=request.method,
            url=str(request.url),
            headers=request.headers,
        )

        # Authenticate the request
        auth_state = self.sdk.authenticate_request(
            # https://github.com/clerk/clerk-sdk-python/issues/116
            httpx_request,  # type: ignore
            AuthenticateRequestOptions(),
        )

        if not auth_state.is_signed_in:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail=auth_state.message
            )

        # Attach the auth state to the request
        request.state.auth_state = auth_state

        return auth_state
