"""Built desktop/mobile journey, routed in-process to the real synthetic API.

Requires locally built test assets and an already cached Playwright browser.
No server is exposed, no browser is downloaded, and requests never leave the
browser route handler. PostgreSQL and Redis belong to the isolated test runner.
"""

from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient
from playwright.sync_api import expect, sync_playwright

import app.routes.visit_prep_interviews as route
from app import root
from app.server import api_app
from app.utils.encryption import decrypt_value

from activemodel.session_manager import get_session
from app.models.visit_prep import VisitPrep
from app.models.visit_prep_response import VisitPrepQuestionResponse
from sqlmodel import select

from tests.services.visit_prep_interviews_test import _fixture


@pytest.mark.parametrize("width,height", [(1440, 1000), (390, 844)])
def test_built_patient_journey_is_private_and_stores_the_original_story(
    monkeypatch, owned_rate_limit_redis, width, height
):
    assert (root / "web/build/test/client/index.html").is_file(), (
        "Build the frontend with synthetic VITE_PYTHON_URL=https://testserver first"
    )
    fixture = _fixture(monkeypatch, f"browser-{width}", recipient_role="caregiver")
    monkeypatch.setattr(
        route.rate_limits, "get_rate_limit_redis", lambda: owned_rate_limit_redis
    )
    # Each scenario is a separate invented transport peer. Earlier full-app
    # journeys use TestClient's default peer and legitimately consume its
    # shared 60-request/8-confirmation buckets during this same test window.
    client = TestClient(
        api_app,
        base_url="https://testserver",
        client=("192.0.2.10" if width == 1440 else "192.0.2.11", 50000),
    )
    requested_urls: list[str] = []
    external_urls: list[str] = []
    response_keys: list[str] = []
    accepted_but_unacknowledged = False
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            args=[
                "--disable-background-networking",
                "--disable-component-update",
                "--host-resolver-rules=MAP * ~NOTFOUND",
                "--proxy-server=http://127.0.0.1:9",
            ]
        )
        try:
            context = browser.new_context(
                viewport={"width": width, "height": height}, service_workers="block"
            )

            def in_process(browser_route):
                nonlocal accepted_but_unacknowledged
                request = browser_route.request
                requested_urls.append(request.url)
                url = urlsplit(request.url)
                if url.scheme != "https" or url.netloc != "testserver":
                    external_urls.append(request.url)
                    browser_route.abort()
                    return
                assert not url.query and not url.fragment
                path = url.path
                assert path == "/visit-prep" or path.startswith(
                    ("/assets/", "/cosmas", "/external/v1/visit-prep")
                ), "The patient page must not bootstrap the account API"
                headers = request.all_headers()
                if path.startswith("/external/v1/visit-prep"):
                    assert headers["authorization"] == f"Bearer {fixture.access_token}"
                    assert "referer" not in headers
                    assert not any(
                        name in headers for name in ("sentry-trace", "baggage")
                    )
                    if path.endswith("/responses"):
                        response_keys.append(headers["idempotency-key"])
                response = client.request(
                    request.method,
                    path,
                    headers={
                        key: value
                        for key, value in headers.items()
                        if key not in {"host", "content-length", "accept-encoding"}
                    },
                    content=request.post_data_buffer,
                )
                if path.endswith("/responses") and not accepted_but_unacknowledged:
                    # Commit the first answer, then lose its acknowledgment.
                    # The browser must retry the original UUID and payload.
                    assert response.status_code == 200
                    accepted_but_unacknowledged = True
                    browser_route.fulfill(status=503, body="Uncertain synthetic save")
                    return
                browser_route.fulfill(
                    status=response.status_code,
                    headers={
                        key: value
                        for key, value in response.headers.items()
                        if key not in {"content-length", "content-encoding"}
                    },
                    body=response.content,
                )

            context.route("**/*", in_process)
            page = context.new_page()
            page.goto(
                f"https://testserver/visit-prep#access_token={fixture.access_token}"
            )
            expect(page.get_by_role("heading", name="Before we begin")).to_be_visible()
            assert page.url == "https://testserver/visit-prep"
            expect(page.get_by_text("Dr. Synthetic", exact=False)).to_have_count(0)
            page.get_by_label("Recipient first name").fill(
                "Jos\N{LATIN SMALL LETTER E WITH ACUTE}"
            )
            page.get_by_label("This invitation was sent to", exact=True).select_option(
                "caregiver"
            )
            page.get_by_role("button", name="Begin Visit Prep").click()
            expect(
                page.get_by_role(
                    "heading", name="What is the main reason for this visit?"
                )
            ).to_be_visible()
            page.get_by_role("textbox").fill(
                "A synthetic reason for the upcoming visit."
            )
            page.get_by_role("button", name="Save and continue").click()
            expect(page.get_by_role("button", name="Retry same save")).to_be_visible()
            expect(page.get_by_role("textbox")).to_have_value(
                "A synthetic reason for the upcoming visit."
            )
            page.get_by_role("button", name="Retry same save").click()
            expect(
                page.get_by_role(
                    "heading", name="What medications are you prescribed right now?"
                )
            ).to_be_visible()
            page.get_by_role("button", name="Skip this topic").click()
            expect(
                page.get_by_role("heading", name="Are you taking them as prescribed?")
            ).to_be_visible()
            page.get_by_role("button", name="Some of them", exact=False).click()
            expect(
                page.get_by_role("heading", name="If not — what got in the way?")
            ).to_be_visible()
            page.get_by_role("textbox").fill("A synthetic barrier.")
            page.get_by_role("button", name="Save and continue").click()
            expect(
                page.get_by_role(
                    "heading",
                    name="Tell me your health story — whatever feels relevant.",
                )
            ).to_be_visible()
            story = "My original synthetic health story, not a model summary."
            page.get_by_role("textbox").fill(story)
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            screenshot_directory = root / "tmp"
            screenshot_directory.mkdir(exist_ok=True)
            page.screenshot(
                path=str(screenshot_directory / f"visit-prep-story-{width}.png"),
                full_page=True,
            )
            assert fixture.access_token not in page.content()
            # Core persists only its nonclinical logger preference; credentials
            # and patient text must not be written to either browser store.
            assert (
                page.evaluate(
                    "JSON.stringify([Object.entries(localStorage), Object.entries(sessionStorage)])"
                )
                == '[[["loglevel","INFO"]],[]]'
            )
            assert page.evaluate("window.Clerk === undefined")
            page.get_by_role("button", name="Save and continue").click()
            expect(
                page.get_by_role("heading", name="Your topics are saved")
            ).to_be_visible()
            page.get_by_role("button", name="Finish Visit Prep").click()
            expect(
                page.get_by_role("heading", name="Visit Prep complete")
            ).to_be_visible()
            expect(
                page.get_by_text(
                    "This does not mean a provider has reviewed them.", exact=False
                )
            ).to_be_visible()
            page.screenshot(
                path=str(screenshot_directory / f"visit-prep-completed-{width}.png"),
                full_page=True,
            )
            assert len(response_keys) == 6 and len(set(response_keys)) == 5
            assert response_keys[0] == response_keys[1]
            assert all(fixture.access_token not in url for url in requested_urls)
            assert all(
                urlsplit(url).netloc == "fonts.googleapis.com" for url in external_urls
            )
            context.close()
        finally:
            browser.close()
    with get_session() as session:
        prep = session.get(VisitPrep, fixture.prep_id)
        assert prep is not None and prep.completed_at is not None
        assert prep.confirmed_by_role == "caregiver"
        responses = session.exec(
            select(VisitPrepQuestionResponse).where(
                VisitPrepQuestionResponse.visit_prep_id == fixture.prep_id
            )
        ).all()
    assert len(responses) == 5
    story_response = next(row for row in responses if row.submission_sequence == 5)
    assert story_response.answer_ciphertext is not None
    assert story not in story_response.answer_ciphertext
    assert story in decrypt_value(story_response.answer_ciphertext)
