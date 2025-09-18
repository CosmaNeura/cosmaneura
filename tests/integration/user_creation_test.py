import time
import pytest

from playwright.sync_api import Page, expect

from app.models.doctor import Doctor

from tests.constants import CLERK_DEV_USER_PASSWORD
from tests.integration.clerk import setup_clerk_testing_token
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

    from app import log

    log.info("Starting test_signup - checking initial database state")
    log.info(f"Doctor count: {Doctor.count()}")

    # paranoid testing to ensure database cleaning is working
    assert Doctor.count() == 0

    log.info("Setting up Clerk testing token")
    setup_clerk_testing_token(page)

    unix_timestamp = int(time.time())
    log.info(f"Generated timestamp: {unix_timestamp}")

    log.info("Navigating to home URL")
    page.goto(home_url())

    log.info("Clicking Sign up button")
    page.get_by_text("Sign up").click()

    log.info("Filling email address")
    page.get_by_label("Email address").click()
    page.get_by_label("Email address").fill(
        f"mike-{unix_timestamp}+clerk_test@example.com"
    )

    log.info("Filling password")
    page.get_by_label("Password", exact=True).fill(CLERK_DEV_USER_PASSWORD)

    log.info("Clicking Continue button")
    page.get_by_role("button", name="Continue", exact=True).click()

    log.info("Waiting for page to load")
    wait_for_loading(page)
        
    log.info("Looking for verification code input")
    try:
        page.get_by_label("Enter verification code. Digit").fill("4")
        page.wait_for_timeout(100)
        page.get_by_label("Digit 2").fill("2")
        page.wait_for_timeout(100)
        page.get_by_label("Digit 3").fill("4")
        page.wait_for_timeout(100)
        page.get_by_label("Digit 4").fill("2")
        page.wait_for_timeout(100)
        page.get_by_label("Digit 5").fill("4")
        page.wait_for_timeout(100)
        page.get_by_label("Digit 6").fill("2")
        log.info("Filled all verification digits successfully")
    except Exception as e:
        log.error(f"Failed to fill verification digits: {e}")
        log.info("Taking screenshot for debugging")
        page.screenshot(path="verification_digits_error.png")
        # Log what input elements are actually on the page
        log.info("Available input elements:")
        inputs = page.locator("input").all()
        for i, input_elem in enumerate(inputs):
            try:
                label = input_elem.get_attribute("aria-label") or input_elem.get_attribute('placeholder') or 'no-label'
                log.info(f"Input {i}: {label}")
            except:
            log.info(f"Input {i}: could not get attributes")
        raise
        
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
