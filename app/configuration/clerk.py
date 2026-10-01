import hashlib
import re

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
