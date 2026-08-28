# pyright: reportOptionalMemberAccess=false

from datetime import datetime, timedelta, timezone

from playwright.sync_api import Page, expect

from app.services.public_legal import CURRENT_LEGAL_DOCUMENTS

from app.models.doctor import Doctor
from app.models.practice import Practice, PracticeMember
from app.models.public_onboarding import PublicLegalAcceptance

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
    Walk a fresh public signup through the BAA and versioned public legal
    acceptances, then prove that a verified billing projection still stops at
    the independent pending-credential boundary.
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

    expect(page.locator("body")).to_contain_text("Review the current terms")
    for document in CURRENT_LEGAL_DOCUMENTS:
        expect(page.locator("body")).to_contain_text(document.version)
        expect(page.locator("body")).to_contain_text(document.sha256)
    expect(page.get_by_role("link", name="Terms of Service")).to_be_visible()
    expect(page.get_by_role("link", name="Privacy Policy")).to_be_visible()
    page.get_by_label("Accepting individual’s name").fill("Integration Tester")
    page.get_by_label("Title").fill("Physician / owner")
    page.get_by_label(
        "I confirm that I am authorized to accept these Terms of Service",
        exact=False,
    ).check()
    page.get_by_role("button", name="Accept current Terms and Privacy").click()
    wait_for_loading(page)

    # A public owner may reach Checkout, but no client-side action grants
    # entitlement. Do not invoke Stripe in this Clerk/browser integration test.
    expect(page.locator("body")).to_contain_text("Start the public pilot")
    expect(page.locator("body")).to_contain_text("$89 per active clinician each month")
    expect(
        page.get_by_role("button", name="Add card and start 14-day trial")
    ).to_be_visible()
    expect(page.locator("body")).not_to_contain_text("Select a patient")

    doctor = Doctor.first()
    assert doctor
    practice = Practice.first()
    assert practice
    member = PracticeMember.get(doctor_id=doctor.id)
    assert member
    assert member.credential_state == "pending"
    assert member.credential_reviewed_at is None
    assert member.credential_reviewer_ref is None
    assert member.credential_verification_method is None
    assert member.credential_evidence_sha256 is None
    assert doctor.onboarding_completed_at is None
    assert practice.stripe_checkout_session_id is None
    assert practice.subscription_status is None
    assert PublicLegalAcceptance.where(
        PublicLegalAcceptance.practice_id == practice.id
    ).count() == len(CURRENT_LEGAL_DOCUMENTS)

    # Project the durable result that Priority 1 owns after a matching
    # Checkout completion and canonical Stripe subscription verification. Test
    # identifiers are inert, no Stripe request is made, and the credential
    # state deliberately remains pending.
    practice.stripe_checkout_session_id = "cs_test_public_onboarding_verified"
    practice.stripe_customer_id = "cus_test_public_onboarding"
    practice.stripe_subscription_id = "sub_test_public_onboarding"
    practice.subscription_status = "trialing"
    practice.stripe_state_version = 1
    practice.trial_ends_at = datetime.now(timezone.utc) + timedelta(days=14)
    practice.save()

    page.reload()
    wait_for_loading(page)

    expect(page.locator("body")).to_contain_text("Credential review is pending")
    expect(page.locator("body")).to_contain_text("14-day trial active")
    expect(page.locator("body")).to_contain_text("PHI access: locked")
    expect(page.locator("body")).to_contain_text(
        "Credential review cannot be self-approved"
    )
    expect(page.locator("body")).not_to_contain_text("Select a patient")
    assert "/onboarding" in page.url

    doctor.refresh()
    practice.refresh()
    member.refresh()
    assert doctor.onboarding_completed_at is None
    assert practice.legal_name.startswith("Riverside")
    assert practice.baa_accepted_at is not None
    assert member.attested_at is not None
    assert member.credential_state == "pending"
