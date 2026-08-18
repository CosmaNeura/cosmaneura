import re
import time
from typing import Protocol

from clerk_backend_api import User
from clerk_backend_api.models import (
    DeletedObject,
    GetUserListRequest,
    GetUserListRequestTypedDict,
)


class ClerkUsersAPI(Protocol):
    def list(
        self,
        *,
        request: GetUserListRequest | GetUserListRequestTypedDict,
    ) -> list[User] | None: ...

    def delete(self, *, user_id: str) -> DeletedObject | None: ...


_CLERK_TEST_USER_EMAIL = re.compile(
    r"test-(?P<created_ns>[0-9]{19})\+clerk_test@example\.com",
    flags=re.ASCII,
)
_CLERK_USER_LIST_LIMIT = 500
_CLERK_TEST_USER_STALE_AFTER_NS = 60 * 60 * 1_000_000_000


def stale_clerk_test_user_id(user: object, *, now_ns: int) -> str | None:
    """Return the id only for a stale ephemeral user created by our UI tests.

    The development Clerk instance is shared with the staging application. An
    inverse allowlist (delete everyone except known accounts) is therefore never
    safe: every new pilot would be destructive cleanup fodder until someone
    edited the test suite.

    Ephemeral integration users are created by ``clerk_test_email()`` with one
    address in a reserved, non-deliverable namespace. Requiring that exact
    shape, a single email, a Clerk-shaped id, and an age beyond the CI job's
    maximum lifetime makes cleanup a positive proof of test ownership without
    deleting another concurrent run's active user. Anything missing or
    unexpected fails closed.
    """

    user_id = getattr(user, "id", None)
    email_addresses = getattr(user, "email_addresses", None)
    if (
        not isinstance(user_id, str)
        or not user_id.startswith("user_")
        or not isinstance(email_addresses, list)
        or len(email_addresses) != 1
    ):
        return None

    email = getattr(email_addresses[0], "email_address", None)
    if not isinstance(email, str) or (
        email_match := _CLERK_TEST_USER_EMAIL.fullmatch(email)
    ) is None:
        return None

    created_ns = int(email_match.group("created_ns"))
    if now_ns - created_ns < _CLERK_TEST_USER_STALE_AFTER_NS:
        return None

    return user_id


def delete_stale_test_users(
    *,
    users_api: ClerkUsersAPI,
    secret_key: str,
    test_environment: bool,
    now_ns: int | None = None,
) -> int:
    """Delete only stale, verifiably test-owned users from a test instance."""

    if not test_environment:
        raise RuntimeError("cannot delete clerk test users outside of testing")

    if not secret_key:
        return 0

    if not secret_key.startswith("sk_test_"):
        raise RuntimeError("refusing clerk cleanup with a non-test secret key")

    if now_ns is None:
        now_ns = time.time_ns()

    users = users_api.list(request={"limit": _CLERK_USER_LIST_LIMIT})
    if users is None:
        raise RuntimeError("clerk returned no user list; refusing cleanup")

    # Select the complete deletion set before mutating Clerk. If inspection of
    # the response fails, no account has been deleted yet.
    user_ids = [
        user_id
        for user in users
        if (user_id := stale_clerk_test_user_id(user, now_ns=now_ns)) is not None
    ]

    for user_id in user_ids:
        users_api.delete(user_id=user_id)

    return len(user_ids)
