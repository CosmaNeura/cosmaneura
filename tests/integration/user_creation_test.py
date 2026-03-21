import pytest
from playwright.sync_api import Page, expect

from app.models.doctor import Doctor

from tests.constants import CLERK_DEV_USER_PASSWORD
from tests.integration.clerk import clerk_test_email, setup_clerk_testing_token
from tests.integration.server import home_url
from tests.integration.utils import login_as_dev_user, wait_for_loading


@pytest.mark.skip(reason="Fails on CI, clerk is requiring email verification")
def test_signin(server, page: Page, assert_snapshot) -> None:
    # paranoid testing to ensure database cleaning is working
    assert Doctor.count() == 0

    _user = login_as_dev_user(page)

    expect(page.locator("body")).to_contain_text("Select a patient")

    wait_for_loading(page)

    assert_snapshot(page)

    assert Doctor.count() == 1


def test_signup(server, page: Page, assert_snapshot) -> None:
    # paranoid testing to ensure database cleaning is working
    assert Doctor.count() == 0

    setup_clerk_testing_token(page)

    test_email = clerk_test_email()

    page.goto(home_url())

    page.get_by_text("Sign up").click()

    page.get_by_label("Email address").click()
    page.get_by_label("Email address").fill(test_email)
    page.get_by_label("Password", exact=True).fill(CLERK_DEV_USER_PASSWORD)
    page.get_by_role("button", name="Continue", exact=True).click()

    wait_for_loading(page)

    page.get_by_label("Enter verification code", exact=True).press_sequentially(
        # slowly type the verification code, it's one single input
        "424242",
        delay=100,
    )

    # TODO assert that `Fill in missing fields` is not displayed

    wait_for_loading(page)

    expect(page.locator("body")).to_contain_text("Select a patient")

    assert_snapshot(page)

    # user should be created at this point!
    assert Doctor.count() == 1

    # logout
    page.locator(".flex > div:nth-child(3)").first.click()
    page.get_by_role("menuitem", name="Sign out").click()

    expect(page.locator("body")).to_contain_text("Sign in")
