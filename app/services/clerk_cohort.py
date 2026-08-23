"""Fail-closed reconstruction of an approved live Clerk user cohort."""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from typing import Any

from app.configuration.clerk import clerk

CLERK_USERS_PAGE_SIZE = 100


class ClerkCohortError(RuntimeError):
    """Raised when the live Clerk population cannot prove the expected cohort."""


@dataclass(frozen=True, slots=True)
class ClerkCohortIdentity:
    """The only identity fields authorized to leave cohort validation."""

    clerk_id: str
    email: str


def _normalize_email(value: object) -> str:
    if not isinstance(value, str):
        raise ClerkCohortError("live Clerk cohort contains an invalid primary email")
    normalized = value.strip().lower()
    if not normalized or len(normalized) > 320 or normalized.count("@") != 1:
        raise ClerkCohortError("live Clerk cohort contains an invalid primary email")
    local, domain = normalized.rsplit("@", 1)
    if not local or not domain or "." not in domain:
        raise ClerkCohortError("live Clerk cohort contains an invalid primary email")
    return normalized


def canonical_email_set_sha256(emails: list[str] | tuple[str, ...]) -> str:
    """Hash normalized unique emails as sorted UTF-8 lines with a final LF."""

    normalized = [_normalize_email(email) for email in emails]
    if len(set(normalized)) != len(normalized):
        raise ClerkCohortError("live Clerk cohort contains duplicate primary emails")
    canonical = "".join(f"{email}\n" for email in sorted(normalized))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _verified_primary_email(user: Any) -> str:
    primary_id = getattr(user, "primary_email_address_id", None)
    addresses = getattr(user, "email_addresses", None)
    if not isinstance(primary_id, str) or not primary_id or not addresses:
        raise ClerkCohortError(
            "live Clerk cohort contains an identity without one verified primary email"
        )
    primary_addresses = [
        address for address in addresses if getattr(address, "id", None) == primary_id
    ]
    if len(primary_addresses) != 1:
        raise ClerkCohortError(
            "live Clerk cohort contains an identity without one verified primary email"
        )
    primary = primary_addresses[0]
    verification = getattr(primary, "verification", None)
    if getattr(verification, "status", None) != "verified":
        raise ClerkCohortError(
            "live Clerk cohort contains an identity without one verified primary email"
        )
    return _normalize_email(getattr(primary, "email_address", None))


def resolve_live_clerk_cohort(
    *,
    expected_count: int,
    expected_sha256: str,
) -> tuple[ClerkCohortIdentity, ...]:
    """Return the exact eligible Clerk cohort only when count and digest match.

    Every page is consumed before the aggregate count and canonical identity-set
    digest are confirmed. Error messages intentionally contain no user fields.
    """

    if isinstance(expected_count, bool) or not isinstance(expected_count, int):
        raise ClerkCohortError("expected_count must be a positive integer")
    if expected_count <= 0:
        raise ClerkCohortError("expected_count must be a positive integer")
    if not isinstance(expected_sha256, str):
        raise ClerkCohortError("expected_sha256 must be a 64-character hex digest")
    digest = expected_sha256.strip().lower()
    if len(digest) != 64 or any(
        character not in "0123456789abcdef" for character in digest
    ):
        raise ClerkCohortError("expected_sha256 must be a 64-character hex digest")

    identities: list[ClerkCohortIdentity] = []
    seen_clerk_ids: set[str] = set()
    seen_emails: set[str] = set()
    offset = 0
    while True:
        try:
            page = clerk.users.list(
                request={
                    "limit": CLERK_USERS_PAGE_SIZE,
                    "offset": offset,
                    "order_by": "+created_at",
                }
            )
        except Exception:
            raise ClerkCohortError("live Clerk cohort retrieval failed") from None
        if page is None:
            raise ClerkCohortError("live Clerk cohort retrieval failed")
        if not page:
            break

        for user in page:
            clerk_id_value = getattr(user, "id", None)
            if not isinstance(clerk_id_value, str) or not clerk_id_value.strip():
                raise ClerkCohortError(
                    "live Clerk cohort contains an invalid Clerk identity"
                )
            clerk_id = clerk_id_value.strip()
            if clerk_id in seen_clerk_ids:
                raise ClerkCohortError(
                    "live Clerk cohort contains duplicate Clerk identities"
                )
            if (
                getattr(user, "banned", None) is not False
                or getattr(user, "locked", None) is not False
            ):
                raise ClerkCohortError(
                    "live Clerk cohort contains an ineligible identity"
                )
            email = _verified_primary_email(user)
            if email in seen_emails:
                raise ClerkCohortError(
                    "live Clerk cohort contains duplicate primary emails"
                )
            seen_clerk_ids.add(clerk_id)
            seen_emails.add(email)
            identities.append(ClerkCohortIdentity(clerk_id=clerk_id, email=email))
        offset += len(page)

    if len(identities) != expected_count:
        raise ClerkCohortError(
            "live Clerk cohort count does not match the approved cohort"
        )
    actual_digest = canonical_email_set_sha256(tuple(seen_emails))
    if not hmac.compare_digest(actual_digest, digest):
        raise ClerkCohortError(
            "live Clerk cohort digest does not match the approved cohort"
        )
    return tuple(sorted(identities, key=lambda identity: identity.email))
