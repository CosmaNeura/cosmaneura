"""Fixture hygiene: ephemeral Clerk JWTs verify locally; expired ones do not.

No network, no env secrets, no Clerk API — only the in-process RSA key from
tests.routes.utils.
"""

import pytest
from clerk_backend_api.security import VerifyTokenOptions, verify_token
from clerk_backend_api.security.types import TokenVerificationError

from tests.routes.utils import (
    MOCK_CLERK_ISSUER,
    MOCK_CLERK_SESSION_ID,
    clerk_test_jwt_public_pem,
    mint_clerk_test_jwt,
)


def _verify(token: str):
    return verify_token(
        token,
        VerifyTokenOptions(
            jwt_key=clerk_test_jwt_public_pem(),
            clock_skew_in_ms=0,
        ),
    )


def test_freshly_minted_clerk_test_jwt_verifies():
    subject = "user_test_fixture_subject"
    token, claims = mint_clerk_test_jwt(subject=subject)

    payload = _verify(token)

    assert payload["sub"] == subject
    assert payload["iss"] == MOCK_CLERK_ISSUER
    assert payload["sid"] == MOCK_CLERK_SESSION_ID
    assert payload["exp"] == claims["exp"]
    assert payload["exp"] > claims["iat"]


def test_expired_clerk_test_jwt_is_rejected():
    token, _claims = mint_clerk_test_jwt(
        subject="user_test_fixture_expired",
        expired=True,
    )

    with pytest.raises(TokenVerificationError):
        _verify(token)
