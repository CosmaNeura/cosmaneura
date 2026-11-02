"""Native capture consumes the bounded verifier owned by Clerk configuration."""

import httpx
from fastapi import HTTPException

from app.configuration.clerk import (
    BoundedNativeClerkVerifier,
    NativeClerkAuthenticationError,
    RequestState,
)

from .clerk import AuthenticateClerkRequest


class AuthenticateNativeClerkRequest(AuthenticateClerkRequest):
    def __init__(self, clerk_secret_key: str):
        super().__init__(clerk_secret_key)
        self._native_verifier = BoundedNativeClerkVerifier(self.sdk)

    def _authenticate_request(self, request: httpx.Request, token: str) -> RequestState:
        try:
            return self._native_verifier.authenticate_request(request, token)
        except NativeClerkAuthenticationError:
            raise HTTPException(
                401, "A verified platform session is required."
            ) from None
