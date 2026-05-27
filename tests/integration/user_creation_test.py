from playwright.sync_api import Page, expect

from app.models.doctor import Doctor

from tests.constants import CLERK_DEV_USER_PASSWORD
from tests.integration.clerk import clerk_test_email, setup_clerk_testing_token
from tests.integration.server import home_url
from tests.integration.utils import login_as_dev_user, wait_for_loading


def test_login_existing_user(server, page: Page) -> None:
    assert Doctor.count() == 0

    doctor = login_as_dev_user(page)

    # login_as_dev_user bypasses the onboarding wizard, so the user should
    # land on the main app surface and not on the welcome screen.
    expect(page.locator("body")).to_contain_text("Select a patient")
    expect(page.locator("body")).not_to_contain_text("Welcome to Cosma")

    assert Doctor.count() == 1
    assert doctor.onboarding_completed_at is not None


def test_signup(server, page: Page) -> None:
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

    wait_for_loading(page)

    # Fresh signups land on the onboarding wizard; the wizard itself is
    # covered end-to-end in test_onboarding_flow.
    expect(page.get_by_role("button", name="Get Started")).to_be_visible()
    assert "/onboarding" in page.url

    assert Doctor.count() == 1
    doctor = Doctor.first()
    assert doctor
    assert doctor.onboarding_completed_at is None
