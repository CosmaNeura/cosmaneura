from datetime import datetime, timezone
from time import monotonic, sleep

from playwright.sync_api import Page

from app.models.doctor import Doctor

from tests.integration.clerk_session import create_dev_user_session
from tests.integration.server import home_url

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


def login_as_dev_user(page: Page) -> Doctor:
    """Log in as the Clerk dev user and return the resulting Doctor.

    Uses the programmatic Clerk session helper to skip the hosted UI
    entirely — see tests/integration/clerk_session.py for the rationale.
    The Doctor row is created the same way as in production: the first
    authenticated request after navigation triggers `inject_doctor_record`.
    """
    # Paranoid testing to ensure database cleaning is working.
    assert Doctor.count() == 0

    create_dev_user_session(page)

    # First authed request after redirect should upsert the Doctor.
    # Short poll closes any tiny race between the redirect landing and
    # the React app's first /api call.
    deadline = monotonic() + 5
    while Doctor.count() == 0 and monotonic() < deadline:
        sleep(0.1)

    assert Doctor.count() == 1, (
        f"expected exactly 1 Doctor after programmatic Clerk session; "
        f"got {Doctor.count()}"
    )

    doctor = Doctor.first()
    assert doctor

    # Bypass the onboarding wizard for tests that focus on post-onboarding
    # surface area: mark onboarding complete AND give the doctor a practice
    # whose BAA is in force (the practice gate has no grandfather path).
    # `page.goto(home_url())` is required after the DB writes so the layout's
    # loader re-fetches settings and stops redirecting to /onboarding.
    from tests.routes.utils import provision_practice_in_force

    doctor.onboarding_completed_at = datetime.now(timezone.utc)
    doctor.save()
    provision_practice_in_force(doctor)
    page.goto(home_url())
    wait_for_loading(page)

    return doctor
