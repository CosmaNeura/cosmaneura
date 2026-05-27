# pyright: reportOptionalMemberAccess=false

from playwright.sync_api import Page, expect

from app import root
from app.models.doctor import Doctor

from tests.constants import CLERK_DEV_USER_PASSWORD
from tests.integration.clerk import clerk_test_email, setup_clerk_testing_token
from tests.integration.server import home_url
from tests.integration.utils import wait_for_loading


def _signup_new_user(page: Page) -> None:
    setup_clerk_testing_token(page)

    page.goto(home_url())

    page.get_by_text("Sign up").click()

    page.get_by_label("Email address").click()
    page.get_by_label("Email address").fill(clerk_test_email())
    page.get_by_label("Password", exact=True).fill(CLERK_DEV_USER_PASSWORD)
    page.get_by_role("button", name="Continue", exact=True).click()

    wait_for_loading(page)

    page.get_by_label("Enter verification code", exact=True).press_sequentially(
        "424242",
        delay=100,
    )

    wait_for_loading(page)


def test_onboarding_flow(server, page: Page) -> None:
    """
    Walk a fresh signup through every step of the onboarding wizard and
    confirm the user lands on /notes with onboarding_completed_at set.

    Without this test, breakage in any of the wizard steps (welcome,
    biography, example note upload, zoom, all-set) would only surface as a
    downstream failure in the tests that bypass onboarding via DB. The
    wizard itself would have no direct coverage.
    """
    assert Doctor.count() == 0

    _signup_new_user(page)

    # Step 0: Welcome
    expect(page.locator("body")).to_contain_text("Welcome to Cosma")
    page.get_by_role("button", name="Get Started").click()

    # Step 1: Biography
    page.get_by_role("textbox", name="Biography").fill(
        "Integration test biography — comprehensive primary care."
    )
    page.get_by_role("button", name="Save & Continue").click()
    wait_for_loading(page)

    # Step 2: Example note upload + note type
    page.get_by_role("textbox", name="Upload Example Visit Note").set_input_files(
        root / "tests/data/example-note.pdf"
    )
    page.locator('[name="note_type"]').click()
    page.locator('[name="note_type"]').fill("Telehealth SOAP Note")
    page.get_by_role("button", name="Upload & Continue").click()
    wait_for_loading(page)

    # Step 3: Zoom. clerk_test_email() returns a random address that is not
    # in the zoom beta list, so the wizard shows the "Coming Soon" variant
    # with a plain Continue button (no real Zoom OAuth round-trip).
    page.get_by_role("button", name="Continue").click()
    wait_for_loading(page)

    # Step 4: All set
    expect(page.locator("body")).to_contain_text("You're all set!")
    page.get_by_role("button", name="Go to Cosma").click()
    wait_for_loading(page)

    expect(page.locator("body")).to_contain_text("Select a patient")
    assert "/notes" in page.url

    doctor = Doctor.first()
    assert doctor
    assert doctor.onboarding_completed_at is not None
    assert doctor.biography
