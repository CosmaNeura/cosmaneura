"""Regression test: the team's staging sign-in accounts must survive the test
suite's Clerk cleanup.

`delete_all_clerk_users()` (run on test-suite startup against the shared dev Clerk
instance that staging also uses) deletes every Clerk user whose email is not in
`CLERK_ALL_USERS_TO_PRESERVE`. If the team's real staging accounts are dropped from
that list, signing in to staging after a deploy again produces NULL-email duplicate
doctors (the app can no longer fetch their email from the deleted Clerk user).
"""

from tests.constants import CLERK_ALL_USERS_TO_PRESERVE, CLERK_STAGING_TEAM_EMAILS


def test_staging_team_accounts_are_preserved_from_clerk_cleanup():
    for email in CLERK_STAGING_TEAM_EMAILS:
        assert email in CLERK_ALL_USERS_TO_PRESERVE
