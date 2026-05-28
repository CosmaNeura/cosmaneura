"""Programmatic Clerk authentication for Playwright integration tests.

`login_as_dev_user` historically drove the Clerk hosted UI in the browser
(fill email, click Continue, fill password, click Continue, wait for the
redirect chain to settle). That redirect chain is flaky in CI — multiple
distinct failure modes were observed across runs on PR #57.

This module sidesteps the UI entirely:

1. The backend SDK mints a one-time `sign_in_token` for the dev user.
2. Clerk returns a redemption URL with the token embedded.
3. We append `redirect_url=<home_url()>` so Clerk redirects the browser
   to our app after consuming the ticket. The redirect carries the
   `__clerk_db_jwt` handshake param that Clerk dev needs to bridge
   sessions cross-origin — without it, the frontend SDK on our domain
   doesn't see the user as signed in.

Prerequisite: the Clerk dev instance must list our test host in its
"Allowed redirect URLs" (e.g. `https://api-test.cosma.localhost/*` or
`https://*.cosma.localhost/*`). Without that config, Clerk falls back
to its hosted "Welcome" page and the session never reaches our app.

`test_onboarding_flow` still uses the UI flow because that test's whole
purpose is to verify the wizard end-to-end.
"""

from urllib.parse import quote

from clerk_backend_api import Clerk
from playwright.sync_api import Page

from tests.constants import CLERK_DEV_USER_EMAIL
from tests.integration.server import home_url
from tests.routes.utils import _get_or_create_clerk_user

# Use a module-level Clerk client so tests can monkeypatch `_clerk` if
# they want to stub. Lazy-initialize on first call so an unset
# CLERK_PRIVATE_KEY at import time doesn't blow up the test collector.
_clerk_client: Clerk | None = None


def _clerk() -> Clerk:
    global _clerk_client
    if _clerk_client is None:
        from app.configuration.clerk import CLERK_PRIVATE_KEY

        if not CLERK_PRIVATE_KEY:
            raise RuntimeError(
                "CLERK_PRIVATE_KEY is required to mint a sign-in token "
                "for the dev user. Make sure your test env exports it."
            )
        _clerk_client = Clerk(bearer_auth=CLERK_PRIVATE_KEY)
    return _clerk_client


def create_dev_user_session(page: Page) -> str:
    """Establish a Clerk session for the dev user on the given page.

    Returns the Clerk user id. After this returns, the page has the
    cookies the React app needs to consider the user logged in;
    subsequent authenticated navigations will succeed.
    """
    user = _get_or_create_clerk_user(CLERK_DEV_USER_EMAIL)
    assert user is not None, (
        f"failed to get or create Clerk user for {CLERK_DEV_USER_EMAIL}"
    )
    assert user.id is not None, f"Clerk user for {CLERK_DEV_USER_EMAIL} has no id"
    user_id: str = user.id

    try:
        token = _clerk().sign_in_tokens.create(request={"user_id": user_id})
    except Exception as e:
        raise RuntimeError(
            f"clerk.sign_in_tokens.create failed for user_id={user_id}: {e}"
        ) from e

    assert token is not None, "sign_in_tokens.create returned None"
    assert token.url, "sign_in_tokens.create returned a token without a redemption url"

    target = home_url()
    separator = "&" if "?" in token.url else "?"
    ticket_url = f"{token.url}{separator}redirect_url={quote(target, safe='')}"

    page.goto(ticket_url)
    # Clerk consumes the ticket, sets session cookies on its domain, and
    # 302s to `target` with the __clerk_db_jwt handshake param appended.
    # Wait until the browser lands on our app's host — if this times out
    # the redirect URL isn't allowlisted in Clerk's dashboard.
    page.wait_for_url(lambda url: url.startswith(target), timeout=10_000)
    page.wait_for_load_state("networkidle")

    return user_id
