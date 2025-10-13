from playwright.sync_api import Page

from app.models.doctor import Doctor

from tests.constants import LONG_INTEGRATION_TEST_TIMEOUT
from tests.integration.clerk import setup_clerk_testing_token
from tests.integration.server import home_url
from tests.routes.utils import get_clerk_dev_user

# TODO scroll to bottom?
# page.evaluate("window.scrollTo(0, document.body.scrollHeight)")

# set_timeout
# page.set_default_timeout(120_000)
# page.set_default_navigation_timeout(120_000)


def wait_for_loading(page: Page, timeout: int = 30000, extreme: bool = False):
    """
    Comprehensive loading detection for modern React 18+ applications, including CSS animations/transitions.

    Parameters:
        page: Playwright page object
        timeout: Maximum wait time in milliseconds
        extreme: Whether to use additional React-specific and animation loading detection
    """
    # Basic page loading states
    page.wait_for_load_state("domcontentloaded")
    page.wait_for_load_state("load")
    page.wait_for_load_state("networkidle")

    # Add React-specific and animation detection when extreme=True
    if extreme:
        page.evaluate("""() => {
            window._reactLoadingComplete = false;

            const checkLoadingComplete = async () => {
                try {
                    // 1. Check for React Suspense fallbacks (React 18+)
                    const suspenseFallbacks = document.querySelectorAll('[data-suspense-fallback="true"]');
                    if (suspenseFallbacks.length > 0) return false;

                    // 2. Check React Router 6+ navigation state
                    if (window.document.documentElement.dataset.reactRouterLoading === 'true') return false;

                    // 3. Check for TanStack Query/React Query loading state
                    if (document.documentElement.dataset.queryLoading === 'true') return false;

                    // 4. Check for common loading indicators
                    const loadingIndicators = document.querySelectorAll(
                        '[aria-busy="true"], .loading-spinner, [data-loading="true"]'
                    );
                    if (loadingIndicators.length > 0) return false;

                    // 5. Wait for any React transitions to complete (React 18+)
                    if (document.documentElement.dataset.reactTransition === 'active') return false;

                    // 6. Wait for CSS animations and transitions to finish
                    const animations = document.getAnimations();
                    if (animations.length > 0) return false;

                    return true;
                } catch (e) {
                    console.error("Error in loading detection:", e);
                    return true; // Prevent hanging on error
                }
            };

            // Poll until loading is complete or timeout occurs
            const startTime = Date.now();
            const poll = async () => {
                if (await checkLoadingComplete()) {
                    window._reactLoadingComplete = true;
                } else if (Date.now() - startTime > 10000) {
                    // Timeout after 10 seconds of polling
                    console.warn("React and animation loading detection timed out");
                    window._reactLoadingComplete = true;
                } else {
                    setTimeout(poll, 100);
                }
            };

            poll();
        }""")

        # Wait for React and animation loading to complete
        try:
            page.wait_for_function(
                "() => window._reactLoadingComplete === true", timeout=timeout
            )
        except Exception as e:
            from app import log

            log.warning(f"React and animation loading check timed out: {e}")

    # Brief pause for any final renders
    page.wait_for_timeout(100)


def login_as_dev_user(page: Page):
    username, password, user = get_clerk_dev_user()

    # paranoid: should not create a doctor record in the DB
    doctor_count = Doctor.count()
    print(f"\n[DEBUG] Doctor count before login: {doctor_count}")
    if doctor_count > 0:
        doctors = Doctor.all()
        print(f"[DEBUG] Existing doctors: {[d.id for d in doctors]}")
    assert doctor_count == 0, f"Expected 0 doctors, found {doctor_count}"

    setup_clerk_testing_token(page)

    page.goto(home_url())

    page.get_by_label("Email address").fill(username)
    page.get_by_role("button", name="Continue", exact=True).click()

    page.get_by_label("Password", exact=True).fill(password)
    page.get_by_role("button", name="Continue").click()

    wait_for_loading(page)

    # The wait command above is brittle and does not guarantee that the clerk login process has finished.
    # However, we know that if we've navigated away from the root URL that we properly logged into the application.
    # In order to provide a more robust loading completion indicator, we assert that the
    # current pages route is not the route that we started on. This will ensure that we've completed the login step and
    # have moved into the application.

    # Debug logging to check our exact current URL

    current_url = page.url
    print(f"[DEBUG] URL before wait_for_url: {current_url}")
    print(f"[DEBUG] Home URL: {home_url()}")
    print(f"[DEBUG] Are they different? {current_url != home_url()}")

    page.wait_for_url(
        lambda url: url != home_url(), timeout=LONG_INTEGRATION_TEST_TIMEOUT
    )

    # Debug logging after the wait_for_url call
    print(f"[DEBUG] URL after wait_for_url: {page.url}")

    # only a single doctor should be created!
    doctor_count_after = Doctor.count()
    print(f"[DEBUG] Doctor count after login: {doctor_count_after}")
    if doctor_count_after > 0:
        doctors = Doctor.all()
        print(f"[DEBUG] Doctors after login: {[d.id for d in doctors]}")
    assert doctor_count_after == 1, (
        f"Expected 1 doctor after login, found {doctor_count_after}"
    )
    doctor = Doctor.first()

    # create note schemas for testing
    create_test_note_schemas(doctor)

    return doctor


def create_test_note_schemas(doctor: Doctor):
    """Create standard note schemas for integration testing."""
    from app.models.doctor_note_schema import DoctorNoteSchema

    # Create Test SOAP Note schema (only if it doesn't exist)
    if not DoctorNoteSchema.where(
        DoctorNoteSchema.doctor_id == doctor.id,
        DoctorNoteSchema.note_type == "Test SOAP Note",
    ).first():
        DoctorNoteSchema(
            doctor_id=doctor.id,
            note_type="Test SOAP Note",
            fields={
                "CC": "Summarize the main reason for the patient's visit in one sentence.",
                "Subjective": "Document the patient's own description of their symptoms, concerns, and any relevant personal or family medical history.",
                "Medications": "List all current medications the patient is taking, including dosages and frequency.",
                "Allergies": "Note any known allergies the patient has, including drug, food, and environmental allergies, along with the type of reaction experienced.",
                "Mental/Functional": "Assess and record the patient's mental status and functional abilities, including any cognitive or psychological concerns.",
                "Vitals": "Record the patient's vital signs, including temperature, pulse, respiration rate, blood pressure, and any other relevant measurements.",
                "Objective": "Include findings from the physical examination and any relevant diagnostic tests or imaging results.",
                "Assessment": "List the primary diagnosis and any secondary diagnoses, along with the rationale for each diagnosis.",
                "Plan": "Outline the proposed management plan, including treatments, medications, follow-up appointments, and any referrals to specialists.",
            },
        ).save()

    # Create General Note schema (only if it doesn't exist)
    if not DoctorNoteSchema.where(
        DoctorNoteSchema.doctor_id == doctor.id,
        DoctorNoteSchema.note_type == "General Note",
    ).first():
        DoctorNoteSchema(
            doctor_id=doctor.id,
            note_type="General Note",
            fields={
                "Chief Complaint": "The main reason for the patient's visit.",
                "History of Present Illness": "Detailed description of the current symptoms and their progression.",
                "Review of Systems": "Systematic review of body systems for additional symptoms.",
                "Past Medical History": "Previous medical conditions and treatments.",
                "Medications": "Current medications including dosages and frequency.",
                "Allergies": "Known allergies and adverse reactions.",
                "Social History": "Lifestyle factors, occupation, and social circumstances.",
                "Family History": "Relevant family medical history.",
                "Physical Examination": "Findings from the physical examination.",
                "Assessment and Plan": "Diagnosis and treatment plan.",
            },
        ).save()
