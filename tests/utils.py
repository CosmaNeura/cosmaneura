from app.configuration.clerk import CLERK_PRIVATE_KEY, clerk
from app.environments import is_testing

from tests.clerk_cleanup import delete_stale_test_users

from .log import log


def delete_stale_clerk_test_users() -> int:
    """Delete only the narrow, verifiably test-owned ephemeral Clerk cohort."""

    log.info("deleting stale ephemeral clerk test users")
    count = delete_stale_test_users(
        users_api=clerk.users,
        secret_key=CLERK_PRIVATE_KEY,
        test_environment=is_testing(),
    )
    log.info("deleted stale ephemeral clerk test users", count=count)
    return count
