import base64
import hashlib
import json
import secrets
import typing as t
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import itsdangerous
import pytest
from cachetools import LRUCache, cached
from clerk_backend_api import CreateSessionRequestBody
from clerk_backend_api import User as ClerkUser
from decouple import config as decouple_config
from fastapi import Request, status
from httpx import Response

from app.configuration.clerk import CLERK_PRIVATE_KEY, AuthStatus, RequestState, clerk
from app.utils.email_domain import extract_email_domain

from app.models.baa import (
    BAA_CURRENT_FILENAME,
    BAA_CURRENT_SHA256,
    BAA_CURRENT_VERSION,
    BAA_DOC_ID,
    BaaAcceptance,
    BaaVersion,
)
from app.models.doctor import Doctor, DoctorRole
from app.models.practice import Practice, PracticeMember
from app.models.superadmin_capability import (
    SuperadminCapabilityEnvironment,
    SuperadminCapabilityGrant,
    SuperadminCapabilityKind,
)

from tests.constants import (
    CLERK_DEV_ADMIN_EMAIL,
    CLERK_DEV_SEED_EMAIL,
    CLERK_DEV_USER_EMAIL,
    CLERK_DEV_USER_PASSWORD,
)

# TODO this is a bit dangerous, let's see how it performs
clerk_cache_instance = LRUCache(maxsize=128)
"to cache user and session keys for all live clerk api interactions"

MOCK_CLERK_ISSUER = "https://resolved-emu-53.clerk.accounts.dev"


@dataclass
class FakeEmailAddress:
    email_address: str


@dataclass
class FakeClerkUser:
    id: str
    email_addresses: list[FakeEmailAddress]


def base_server_url(protocol: t.Literal["http", "https"] = "https"):
    """
    VITE_PYTHON_URL is defined as the protocol + host, but the user/dev shouldn't have to worry
    about trailing slash, etc so we normalize it here.

    Note that the scheme is really important. The cookie middleware is setup to require
    https in order to set receive cookies, so if `http` is used instead of `https` this will
    break tests which require cookies.

    `https` requires that localias is setup, otherwise http (or some other SSL mechanism) must be used.
    """

    url = decouple_config("VITE_PYTHON_URL", cast=str).strip()

    # Remove any existing protocol
    if url.startswith(("http://", "https://")):
        url = url.split("://")[1]

    # Remove any trailing slashes
    url = url.rstrip("/")

    # Add protocol and trailing slash
    return f"{protocol}://{url}/"


def _build_local_clerk_user(email: str) -> FakeClerkUser:
    digest = hashlib.sha1(email.encode()).hexdigest()[:16]
    return FakeClerkUser(
        id=f"user_{digest}",
        email_addresses=[FakeEmailAddress(email_address=email)],
    )


@cached(cache=clerk_cache_instance)
def _get_or_create_clerk_user(email: str):
    if not CLERK_PRIVATE_KEY:
        return _build_local_clerk_user(email)

    user_list = clerk.users.list(request={"email_address": [email]})
    assert user_list is not None

    if len(user_list) == 1:
        return user_list[0]
    elif len(user_list) == 0:
        return clerk.users.create(
            request={
                "email_address": [email],
                "password": CLERK_DEV_USER_PASSWORD,
            }
        )
    else:
        raise ValueError("more than one user found")


def get_clerk_dev_user():
    """
    Get or generate a common dev user to login via clerk
    """
    user = _get_or_create_clerk_user(CLERK_DEV_USER_EMAIL)
    assert user

    return CLERK_DEV_USER_EMAIL, CLERK_DEV_USER_PASSWORD, user


def ensure_current_baa_version() -> BaaVersion:
    """(Re)create the current BAA version row from the model constants.

    The migration seeds it in real databases, but the test harness truncates
    every table at session start (and again before each integration test), so
    test setup must be able to restore it."""

    version = BaaVersion.get(doc_id=BAA_DOC_ID, is_current=True)
    if version:
        return version
    return BaaVersion(
        doc_id=BAA_DOC_ID,
        version=BAA_CURRENT_VERSION,
        sha256=BAA_CURRENT_SHA256,
        content_path=f"app/assets/baa/{BAA_CURRENT_FILENAME}",
        published_at=datetime.now(timezone.utc),
        is_current=True,
    ).save()


def provision_practice_in_force(doctor: Doctor) -> Practice:
    """Give a doctor an owner membership in a practice whose BAA is in force
    and which is entitled — the state every PHI-bearing route requires under
    the practice gate. Idempotent per doctor."""

    member = PracticeMember.get(doctor_id=doctor.id)
    if member:
        practice = Practice.get(id=member.practice_id)
        assert practice
        return practice

    version = ensure_current_baa_version()

    now = datetime.now(timezone.utc)
    practice = Practice(
        legal_name="Test Practice, PLLC",
        join_code=f"T{secrets.token_hex(4).upper()[:7]}",
        email_domain=extract_email_domain(doctor.email) or "example.com",
        origin_channel="direct",
        baa_version=f"{version.doc_id} v{version.version}",
        baa_accepted_at=now,
        baa_accepted_by_doctor_id=doctor.id,
        baa_signature_name="Test Owner",
        baa_signature_title="Physician / owner",
        billing_exempt=True,
    ).save()
    BaaAcceptance(
        practice_id=practice.id,
        baa_version_id=version.id,
        covered_entity_legal_name=practice.legal_name,
        accepted_by_doctor_id=doctor.id,
        accepted_by_name="Test Owner",
        accepted_by_title="Physician / owner",
        accepted_by_email=doctor.email or "test@example.com",
        accepted_at_utc=now,
        content_sha256=version.sha256,
    ).save()
    PracticeMember(
        practice_id=practice.id,
        doctor_id=doctor.id,
        role="owner",
        attested_at=now,
        attestation_name="Test Owner",
    ).save()
    return practice


def grant_test_superadmin_capability(
    doctor: Doctor,
    capability_kind: SuperadminCapabilityKind,
    *,
    environment: SuperadminCapabilityEnvironment = (
        SuperadminCapabilityEnvironment.staging
    ),
    issuer: str = MOCK_CLERK_ISSUER,
) -> SuperadminCapabilityGrant:
    """Create one exact test-only grant without invoking live Clerk."""

    assert doctor.email
    now = datetime.now(timezone.utc)
    return SuperadminCapabilityGrant(
        environment=environment,
        capability_kind=capability_kind,
        clerk_issuer=issuer,
        clerk_subject=doctor.clerk_id,
        doctor_id=doctor.id,
        issuer_sha256=hashlib.sha256(issuer.encode()).hexdigest(),
        subject_sha256=hashlib.sha256(doctor.clerk_id.encode()).hexdigest(),
        verified_primary_email_sha256=hashlib.sha256(
            doctor.email.strip().lower().encode()
        ).hexdigest(),
        source_ref="controlled:route-test",
        source_sha256="c" * 64,
        grant_plan_sha256="d" * 64,
        granted_by_ref="ticket:route-test",
        grant_reason_code="reviewed_test_grant",
        granted_at=now - timedelta(minutes=1),
    ).save()


def get_clerk_seed_user():
    """
    Get or generate a common dev user to login via clerk. Creates a local user record with a seed role.

    This user is used in both test and development environments.
    """
    user = _get_or_create_clerk_user(CLERK_DEV_SEED_EMAIL)
    assert user

    return CLERK_DEV_USER_EMAIL, CLERK_DEV_USER_PASSWORD, user


def get_clerk_admin_user():
    """
    Get or generate a common admin user to login via clerk. Creates a local user record with an admin role.
    """
    user = _get_or_create_clerk_user(CLERK_DEV_ADMIN_EMAIL)
    assert user

    # create the admin user locally
    _admin = Doctor.find_or_create_by(
        clerk_id=user.id, email=CLERK_DEV_ADMIN_EMAIL, role=DoctorRole.admin
    )

    return CLERK_DEV_ADMIN_EMAIL, CLERK_DEV_USER_PASSWORD, user


class MockAuthenticateRequest:
    "mock out the clerk interactions, note that `authenticated_client` must be used here"

    # Optionally override the clerk user.
    def __init__(self, user: ClerkUser | None = None):
        self._user = user

    async def __call__(self, request: Request) -> RequestState:
        if not self._user:
            _, _, user = get_clerk_dev_user()
        else:
            user = self._user

        fake_auth_state = RequestState(
            status=AuthStatus.SIGNED_IN,
            reason=None,
            token="eyJhbGciOiJSUzI1NiIsImNhdCI6ImNsX0I3ZDRQRDExMUFBQSIsImtpZCI6Imluc18yb25uM1E3TkRmcndDNW9WWGd6R2U2dkExdTQiLCJ0eXAiOiJKV1QifQ.eyJleHAiOjE3MzY3ODk1ODgsImZ2YSI6Wy0xLC0xXSwiaWF0IjoxNzM2Nzg5NTI4LCJpc3MiOiJodHRwczovL3Jlc29sdmVkLWVtdS01My5jbGVyay5hY2NvdW50cy5kZXYiLCJuYmYiOjE3MzY3ODk1MTgsInNpZCI6InNlc3NfMnJhRnh2anVDUE5QRVFxaE8wdlFyMjVncjlvIiwic3ViIjoidXNlcl8ycmFGeHAzRENWUkZobnpqSlZpSDQ0Y2l2d3UifQ.DgnSSybWpG4ovKIDwDEvtnpMYOz8toWOJf8GXuftPIa0RiWp8EWpiX_m6fCTQBT3oCoR_BQXOEfTHtutzMYT5zmEL7FzDqBg_dE_dH9LJlq4s9WJ49pjWr1gfdMJF1okf0vdfROdKYnlCiKO4ocP1gM6s8hOPjRp_FTlFcj5bHhKSvRW63gmbeH-G-mZvxVkmXV7g9FeZ3idtSvzap8fBawYmhhqmtEa8-Re2Ikd1v3FxmnMcV2HPcKt1Kv9Vuo7oTC4_uMiX2MS-2EYt0Y5H6bkCyvbEmWmznIx2ZEnVQYFS36gpvjayEnr0uFK_jE1Uc7MIMgybueDqEcA1zqBjQ",
            payload={
                "exp": 1736789588,
                "fva": [-1, -1],
                "iat": 1736789528,
                # TODO should be dynamic
                "iss": "https://resolved-emu-53.clerk.accounts.dev",
                "nbf": 1736789518,
                "sid": "sess_2raFxvjuCPNPEQqhO0vQr25gr9o",
                "sub": user.id,
            },
        )
        # Attach the fake auth state to the request
        request.state.auth_state = fake_auth_state
        return fake_auth_state


@cached(cache=clerk_cache_instance, key=lambda user=None: getattr(user, "id", None))
def get_valid_token(user: ClerkUser | FakeClerkUser | None = None):
    "get a valid clerk session for a clerk user, the local user does not need to be created"

    if not CLERK_PRIVATE_KEY:
        pytest.skip("requires live Clerk access; set CLERK_PRIVATE_KEY for these tests")

    if not user:
        _, _, user = get_clerk_dev_user()

    # now that we have a user, we need to create a session
    assert user.id
    session = clerk.sessions.create(request=CreateSessionRequestBody(user_id=user.id))
    assert session

    token = clerk.sessions.create_token(session_id=session.id)
    assert token

    return token.jwt


def decode_cookie(response: Response):
    "decode a signed cookie into a dict for inspection and assertion"
    from app.routes.middleware import SESSION_SECRET_KEY

    signer = itsdangerous.TimestampSigner(str(SESSION_SECRET_KEY))
    encrypted_cookie_value = response.cookies.get("session")

    if not encrypted_cookie_value:
        return {}

    decoded = signer.unsign(encrypted_cookie_value, max_age=14 * 24 * 60 * 60)
    session_data = json.loads(base64.b64decode(decoded))
    return session_data


def assert_status(response, status_code: int = status.HTTP_200_OK):  # noqa: F821
    assert response.status_code == status_code, response.json()
