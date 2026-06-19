from pathlib import Path

from decouple import config

CLERK_DEV_USER_PASSWORD = "clerk-development-123"

CLERK_QA_USER_EMAIL = "qa+clerk_test@example.com"
"not used in the test suite, but used for dev login"

CLERK_DEV_USER_EMAIL = "user+clerk_test@example.com"
"plain old normal user"

CLERK_DEV_ADMIN_EMAIL = "admin+clerk_test@example.com"
"user with an admin role set locally"

CLERK_DEV_SEED_EMAIL = "seed+clerk_test@example.com"
"user with seed data attached"

# Real accounts the team uses to sign in to the STAGING app. They are not part of
# the four clerk_test seed accounts above, so without this the test suite's
# delete_all_clerk_users() wipes them from the shared dev Clerk instance on every
# CI run (i.e. every deploy). After that wipe the app can no longer fetch their
# email from Clerk (clerk.users.get -> resource_not_found), so the next sign-in
# creates a duplicate doctor with a NULL email. Preserving them keeps staging
# sign-ins stable. This is test-only config; it does not affect production
# runtime, and production uses a separate `live` Clerk instance tests never touch.
CLERK_STAGING_TEAM_EMAILS = [
    "dev@cosmaneura.com",
    "ortegaoscar14@gmail.com",
]

CLERK_ALL_USERS_TO_PRESERVE = [
    CLERK_DEV_USER_EMAIL,
    CLERK_DEV_ADMIN_EMAIL,
    CLERK_QA_USER_EMAIL,
    CLERK_DEV_SEED_EMAIL,
    *CLERK_STAGING_TEAM_EMAILS,
]

TMP_DIRECTORY = config("TMP_DIRECTORY", cast=Path)

# this entire folder is uploaded as an artifact
TEST_RESULTS_DIRECTORY = config("TEST_RESULTS_DIRECTORY", cast=Path)

PYTHON_TEST_SERVER_HOST = config("PYTHON_TEST_SERVER_HOST", cast=str)
