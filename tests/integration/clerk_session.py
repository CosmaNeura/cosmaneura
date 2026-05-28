"""Programmatic Clerk authentication for Playwright integration tests.

`login_as_dev_user` historically drove the Clerk hosted UI in the browser
(fill email, click Continue, fill password, click Continue, wait for the
redirect chain to settle). That redirect chain is flaky in CI — multiple
distinct failure modes were observed across runs on PR #57.

This module sidesteps the UI and the Clerk API entirely:

1. We inject `window.__TEST_AUTH__ = { userId }` via Playwright's
   add_init_script BEFORE navigating to the app.
2. The frontend's getClient() reads that, returns a stub Clerk client
   whose getToken yields a sentinel `test:<userId>` bearer.
3. The backend's `AuthenticateClerkRequest` honors that bearer only when
   PYTHON_ENV=test (and the id has the required prefix); prod and
   staging fall through to real Clerk verification.

This trades a small amount of test-only code in production paths for
deterministic auth in CI. The branches are env-gated at both layers:
backend by `is_testing()`, frontend by `VITE_ENABLE_TEST_AUTH`.

`test_onboarding_flow` still uses the UI flow because that test's whole
purpose is to verify the wizard end-to-end.
"""

from playwright.sync_api import Page

from tests.integration.server import home_url

# Stable id for the dev user across the test suite. The leading
# `user_test_` prefix is required by the backend bypass to ensure prod
# Clerk ids (`user_2xx…`) can never be impersonated through this path.
DEV_USER_TEST_CLERK_ID = "user_test_dev"


def create_dev_user_session(page: Page) -> str:
    """Install the test-auth bypass on the page and navigate to home.

    Returns the Clerk user id used. After this returns, the React app
    is mounted as an authenticated user; the first authed backend
    request will trigger `inject_doctor_record` and create the Doctor
    row.
    """
    page.add_init_script(
        "window.__TEST_AUTH__ = { userId: %r };" % DEV_USER_TEST_CLERK_ID
    )
    page.goto(home_url())
    page.wait_for_load_state("networkidle")
    return DEV_USER_TEST_CLERK_ID
