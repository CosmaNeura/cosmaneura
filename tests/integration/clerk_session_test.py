"""Unit test for the programmatic Clerk session helper.

These tests stub the Clerk SDK and the Playwright Page so they run as
fast unit tests (no network, no browser). The real end-to-end behavior
is exercised by the integration tests that depend on
`login_as_dev_user`.
"""

from typing import cast
from unittest.mock import MagicMock

from flexmock import flexmock
from playwright.sync_api import Page

from tests.integration import clerk_session
from tests.integration.clerk_session import create_dev_user_session


def test_create_dev_user_session_mints_token_and_navigates_to_url():
    fake_user = MagicMock(id="user_test_abc123")
    fake_token = MagicMock(
        url="https://example.accounts.dev/sign-in?__clerk_ticket=tkt_x"
    )

    flexmock(clerk_session).should_receive("_get_or_create_clerk_user").and_return(
        fake_user
    )

    sign_in_tokens = flexmock()
    sign_in_tokens.should_receive("create").with_args(
        request={"user_id": "user_test_abc123"}
    ).and_return(fake_token).once()
    flexmock(clerk_session).should_receive("_clerk").and_return(
        flexmock(sign_in_tokens=sign_in_tokens)
    )

    flexmock(clerk_session).should_receive("home_url").and_return(
        "https://api-test.cosma.localhost/"
    )

    expected_redirected = (
        "https://example.accounts.dev/sign-in?__clerk_ticket=tkt_x"
        "&redirect_url=https%3A%2F%2Fapi-test.cosma.localhost%2F"
    )

    page = flexmock()
    page.should_receive("goto").with_args(expected_redirected).once()
    page.should_receive("wait_for_url").with_args(object, timeout=10_000).once()
    page.should_receive("wait_for_load_state").with_args("networkidle").once()

    user_id = create_dev_user_session(cast(Page, page))

    assert user_id == "user_test_abc123"


def test_create_dev_user_session_raises_when_token_creation_fails():
    fake_user = MagicMock(id="user_test_abc123")
    flexmock(clerk_session).should_receive("_get_or_create_clerk_user").and_return(
        fake_user
    )

    sign_in_tokens = flexmock()
    sign_in_tokens.should_receive("create").and_raise(RuntimeError("Clerk 500"))
    flexmock(clerk_session).should_receive("_clerk").and_return(
        flexmock(sign_in_tokens=sign_in_tokens)
    )

    page = flexmock()

    try:
        create_dev_user_session(cast(Page, page))
        raise AssertionError("expected RuntimeError")
    except RuntimeError as e:
        assert "Clerk 500" in str(e)
