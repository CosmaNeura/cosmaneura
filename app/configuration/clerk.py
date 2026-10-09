import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone

from clerk_backend_api import Clerk
from clerk_backend_api.models import ClerkError, ClerkErrors, ClerkErrorsData
from clerk_backend_api.security import (
    AuthenticateRequestOptions as AuthenticateRequestOptions,
)
from clerk_backend_api.security import (
    AuthStatus as AuthStatus,
)
from clerk_backend_api.security import (
    RequestState as RequestState,
)
from decouple import config

from app.configuration import authority as authority_configuration
from app.environments import python_environment
from app.services.practice_owner_identity_contract import OwnerIdentityAbsence

CLERK_PRIVATE_KEY = config("CLERK_PRIVATE_KEY", cast=str)

clerk = Clerk(
    bearer_auth=CLERK_PRIVATE_KEY,
)

# TODO add debug logger configuration

MANUAL_QA_SEED = "cosmaneura-staging-manual-qa-v1"
MANUAL_QA_EMAILS = ("biller@cosmaneura.com", "doctor@cosmaneura.com")


def manual_qa_identity_owned(clerk_id: str) -> bool:
    """Return only the ownership fact; callers use normal email/access checks."""
    from app.environments import is_staging

    if not is_staging() or not CLERK_PRIVATE_KEY.startswith("sk_test_"):
        return False
    user = clerk.users.get(user_id=clerk_id)
    return bool(
        user is not None
        and user.id == clerk_id
        and isinstance(user.private_metadata, Mapping)
        and user.private_metadata.get("manual_qa_seed") == MANUAL_QA_SEED
    )


def provision_manual_qa_users(passwords: dict[str, str]) -> dict[str, str]:
    """Create only owned, staging test identities; never adopt or reset a user.

    Existing users must retain the seed marker and verified primary address.
    Preflight both addresses before the first write. A partial provider failure
    is recoverable by rerunning: already-created users are left unchanged.
    """
    from app.environments import is_staging

    if not is_staging() or not CLERK_PRIVATE_KEY.startswith("sk_test_"):
        raise ValueError("Manual QA identities require staging and a Clerk test key")
    if set(passwords) != set(MANUAL_QA_EMAILS) or any(
        len(password) < 16 for password in passwords.values()
    ):
        raise ValueError("Both QA passwords must contain at least 16 characters")
    existing = {}
    for email in MANUAL_QA_EMAILS:
        users = clerk.users.list(request={"email_address": [email], "limit": 2})
        if len(users) > 1:
            raise ValueError("Ambiguous QA identity; no accounts changed")
        if users:
            user = users[0]
            primary = [
                address
                for address in user.email_addresses
                if address.id == user.primary_email_address_id
            ]
            if (
                not isinstance(user.private_metadata, Mapping)
                or user.private_metadata.get("manual_qa_seed") != MANUAL_QA_SEED
                or user.banned is not False
                or user.locked is not False
                or len(primary) != 1
                or primary[0].email_address != email
                or primary[0].verification is None
                or primary[0].verification.status != "verified"
            ):
                raise ValueError(
                    "Existing QA address is not a usable seed-owned identity"
                )
            existing[email] = user.id
    for email in MANUAL_QA_EMAILS:
        if email not in existing:
            user = clerk.users.create(
                email_address=[email],
                password=passwords[email],
                first_name="Synthetic QA",
                last_name="Owner" if email.startswith("biller@") else "Clinician",
                private_metadata={"manual_qa_seed": MANUAL_QA_SEED},
            )
            if user is None:
                raise ValueError("Clerk did not return a QA identity; rerun safely")
            existing[email] = user.id
    return existing

def _owner_identity_provenance() -> tuple[str, str] | None:
    environment = python_environment().strip().lower()
    if environment not in {"development", "test", "staging", "production"}:
        return None
    settings = authority_configuration.AUTHORITY_KERNEL_SETTINGS
    if not settings.has_valid_clerk_issuer():
        return None
    return environment, hashlib.sha256(
        settings.clerk_issuer.encode("utf-8")
    ).hexdigest()


def prove_absent_owner_identity(subject: str) -> OwnerIdentityAbsence | None:
    """Prove one current configured-issuer absence, never infer it from failure.

    Only the SDK's typed, exact 404 resource-not-found response is evidence.
    This single read has no retries and returns no provider error details.
    """
    if not isinstance(subject, str) or not re.fullmatch(
        r"user_[A-Za-z0-9]{1,128}", subject
    ):
        return None
    try:
        provenance = _owner_identity_provenance()
        if provenance is None:
            return None
        configured_client = clerk
        try:
            configured_client.users.get(user_id=subject, retries=None, timeout_ms=6000)
        except ClerkErrors as error:
            if type(error.status_code) is not int or error.status_code != 404:
                return None
            data = error.data
            if not isinstance(data, ClerkErrorsData):
                return None
            errors = data.errors
            if (
                not isinstance(errors, list)
                or len(errors) != 1
                or not isinstance(errors[0], ClerkError)
                or type(errors[0].code) is not str
                or errors[0].code != "resource_not_found"
            ):
                return None
        else:
            # A None result or even a malformed object is not absence evidence.
            return None
        if clerk is not configured_client or _owner_identity_provenance() != provenance:
            return None
        environment, issuer_sha256 = provenance
        return OwnerIdentityAbsence(
            subject=subject,
            environment=environment,
            issuer_sha256=issuer_sha256,
        )
    except Exception:
        return None


@dataclass(frozen=True)
class VerifiedProviderSession:
    issuer: str
    subject: str
    session_sha256: str
    expires_at: datetime | None = None


def verified_provider_session(
    auth_state, *, expected_issuer: str = ""
) -> VerifiedProviderSession:
    """Normalize an already-verified SDK state; never parse caller headers here."""
    payload = getattr(auth_state, "payload", None)
    if not getattr(auth_state, "is_signed_in", False) or not isinstance(payload, dict):
        raise ValueError("provider_session_invalid")
    issuer, subject, sid = (payload.get(k) for k in ("iss", "sub", "sid"))
    if (
        not isinstance(issuer, str)
        or not isinstance(subject, str)
        or not isinstance(sid, str)
    ):
        raise ValueError("provider_session_invalid")
    if (
        not all(
            1 <= len(v) <= 512 and not any(ord(c) < 32 or ord(c) == 127 for c in v)
            for v in (issuer, subject, sid)
        )
        or not subject.startswith("user_")
        or (expected_issuer and issuer != expected_issuer)
    ):
        raise ValueError("provider_session_invalid")
    exp = payload.get("exp")
    if exp is not None and (type(exp) is not int or exp <= 0):
        raise ValueError("provider_session_invalid")
    try:
        expires_at = (
            datetime.fromtimestamp(exp, timezone.utc) if exp is not None else None
        )
    except (ValueError, OverflowError, OSError):
        raise ValueError("provider_session_invalid") from None
    return VerifiedProviderSession(
        issuer, subject, hashlib.sha256(sid.encode()).hexdigest(), expires_at
    )
