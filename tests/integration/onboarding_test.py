# pyright: reportOptionalMemberAccess=false

from playwright.sync_api import Page, expect

from app.models.doctor import Doctor
from app.models.practice import Practice, PracticeMember

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
    Walk a fresh signup through practice naming + Cosmaneura Inc BAA, skip
    optional biography/PDF/Zoom, and land on /notes with the BAA in force.
    """
    assert Doctor.count() == 0

    _signup_new_user(page)

    expect(page.locator("body")).to_contain_text("How will this Cosmas account be used")
    page.get_by_role("button", name="This is my practice").click()

    page.get_by_label("Legal name of the covered entity").fill(
        "Riverside Family Practice, PLLC"
    )
    page.get_by_role("button", name="Continue to the BAA").click()
    wait_for_loading(page)

    expect(page.locator("body")).to_contain_text("Business Associate Agreement")
    # the canonical, hash-verified CN-BAA-CUST-CW text is served by the
    # backend and must render before acceptance is possible
    expect(page.locator("body")).to_contain_text("HIPAA BUSINESS ASSOCIATE AGREEMENT")
    expect(page.locator("body")).to_contain_text("CN-BAA-CUST-CW")
    page.get_by_label("Accepting individual’s name").fill("Integration Tester")
    page.get_by_label("Title").fill("Physician / owner")
    page.get_by_label(
        "I have read and agree to the HIPAA Business Associate Agreement"
    ).check()
    page.get_by_role("button", name="I Agree").click()
    wait_for_loading(page)

    expect(page.locator("body")).to_contain_text("Optional setup")
    page.get_by_role("button", name="Start using Cosmas").click()

    expect(page.locator("body")).to_contain_text("You're all set!")
    expect(page.locator("body")).to_contain_text("Workforce must sign in from @")
    page.get_by_role("button", name="Go to Cosmas").click()
    wait_for_loading(page)

    expect(page.locator("body")).to_contain_text("Select a patient")
    assert "/notes" in page.url

    doctor = Doctor.first()
    assert doctor
    assert doctor.onboarding_completed_at is not None
    practice = Practice.first()
    assert practice
    assert practice.legal_name.startswith("Riverside")
    assert practice.baa_accepted_at is not None
    member = PracticeMember.get(doctor_id=doctor.id)
    assert member
    assert member.attested_at is not None
