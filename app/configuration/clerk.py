from collections.abc import Mapping

from clerk_backend_api import Clerk
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
