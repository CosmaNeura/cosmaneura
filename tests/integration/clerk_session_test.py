"""Unit test for the programmatic Clerk session helper.

These tests stub the Playwright Page so they run as fast unit tests
(no network, no browser). The real end-to-end behavior is exercised by
the integration tests that depend on `login_as_dev_user`.
"""

from typing import cast

from flexmock import flexmock
from playwright.sync_api import Page

from tests.integration import clerk_session
from tests.integration.clerk_session import (
    DEV_USER_TEST_CLERK_ID,
    create_dev_user_session,
)


def test_create_dev_user_session_installs_bypass_and_navigates_home():
    flexmock(clerk_session).should_receive("home_url").and_return(
        "https://api-test.cosma.localhost/"
    )

    expected_script = "window.__TEST_AUTH__ = { userId: %r };" % DEV_USER_TEST_CLERK_ID

    page = flexmock()
    page.should_receive("add_init_script").with_args(expected_script).once()
    page.should_receive("goto").with_args("https://api-test.cosma.localhost/").once()
    page.should_receive("wait_for_load_state").with_args("networkidle").once()

    user_id = create_dev_user_session(cast(Page, page))

    assert user_id == DEV_USER_TEST_CLERK_ID
    assert user_id.startswith("user_test_")
