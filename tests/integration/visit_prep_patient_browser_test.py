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
from app.models.authority import AuthorityAuditEvent
from app.models.visit_prep import VisitPrep
from app.models.visit_prep_contribution import VisitPrepContributionEvent
from app.models.visit_prep_response import VisitPrepQuestionResponse
from sqlmodel import col, select

from tests.services.visit_prep_interviews_test import _fixture


@pytest.mark.parametrize("width,height", [(1440, 1000), (390, 844)])
@pytest.mark.parametrize("initial_adherence", ["some", "yes"])
def test_built_patient_journey_is_private_and_stores_the_original_story(
    monkeypatch, owned_rate_limit_redis, width, height, initial_adherence
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
        client=(
            f"203.0.113.{(10 if width == 1440 else 11) + (20 if initial_adherence == 'yes' else 0)}",
            50000,
        ),
    )
    requested_urls: list[str] = []
    external_urls: list[str] = []
    response_keys: list[str] = []
    accepted_but_unacknowledged = False
    correction_keys: list[str] = []
    correction_bodies: list[bytes] = []
    correction_unacknowledged = False
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
                nonlocal accepted_but_unacknowledged, correction_unacknowledged
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
                    elif path.endswith("/contribution/response"):
                        correction_keys.append(headers["idempotency-key"])
                        correction_bodies.append(request.post_data_buffer)
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
                if (
                    path.endswith("/contribution/response")
                    and not correction_unacknowledged
                ):
                    assert response.status_code == 200
                    correction_unacknowledged = True
                    browser_route.fulfill(
                        status=503, body="Uncertain synthetic correction"
                    )
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
            with get_session() as session:
                opens = session.exec(
                    select(AuthorityAuditEvent).where(
                        AuthorityAuditEvent.resource_ref == str(fixture.prep_id),
                        AuthorityAuditEvent.event_type
                        == "visit_prep.invitation_flow_opened",
                    )
                ).all()
                assert len(opens) == 1
                assert opens[0].actor_id is opens[0].person_id is None
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
            if initial_adherence == "some":
                page.get_by_role("button", name="Some of them", exact=False).click()
                expect(
                    page.get_by_role("heading", name="If not — what got in the way?")
                ).to_be_visible()
                page.get_by_role("textbox").fill("A synthetic barrier.")
                page.get_by_role("button", name="Save and continue").click()
            else:
                page.get_by_role(
                    "button", name="Yes, as prescribed", exact=False
                ).click()
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
            page.get_by_role("button", name="Review or change saved answers").click()
            expect(
                page.get_by_role("heading", name="Review your saved answers")
            ).to_be_visible()
            expect(page.get_by_text(story, exact=True)).to_have_count(0)
            page.get_by_label("Recipient first name").fill("José")
            page.get_by_label("This invitation was sent to", exact=True).select_option(
                "caregiver"
            )
            page.get_by_role("button", name="Show saved answers").click()
            expect(
                page.get_by_role("heading", name="Your saved answers")
            ).to_be_visible()
            expect(page.get_by_text(story, exact=True)).to_be_visible()
            revised_story = "My corrected synthetic story, with the original retained."
            if initial_adherence == "some":
                page.get_by_role(
                    "button",
                    name="Change: Tell me your health story — whatever feels relevant.",
                    exact=True,
                ).click()
                expect(page.get_by_role("textbox")).to_have_value(story)
                page.get_by_role("textbox").fill(revised_story)
                page.get_by_role("button", name="Save and continue").click()
            else:
                expect(
                    page.get_by_role("heading", name="If not — what got in the way?")
                ).to_have_count(0)
                page.get_by_role(
                    "button",
                    name="Change: Are you taking them as prescribed?",
                    exact=True,
                ).click()
                page.get_by_role("button", name="Some of them", exact=False).click()
            expect(page.get_by_role("button", name="Retry same save")).to_be_visible()
            if initial_adherence == "some":
                expect(page.get_by_role("textbox")).to_be_disabled()
            else:
                expect(
                    page.get_by_role("button", name="Some of them", exact=False)
                ).to_be_disabled()
            page.get_by_role("button", name="Retry same save").click()
            if initial_adherence == "some":
                expect(page.get_by_text(revised_story, exact=True)).to_be_visible()
                page.get_by_text("Earlier wording retained", exact=True).click()
                expect(page.get_by_text(story, exact=True)).to_be_visible()
            else:
                expect(
                    page.get_by_text("Not answered yet.", exact=True)
                ).to_be_visible()
                expect(
                    page.get_by_role("button", name="Confirm my answers")
                ).to_be_disabled()
                page.get_by_role(
                    "button", name="Answer: If not — what got in the way?", exact=True
                ).click()
                page.get_by_role("button", name="Skip this topic").click()
                expect(
                    page.get_by_role("heading", name="Your saved answers")
                ).to_be_visible()
                expect(
                    page.get_by_text("You skipped this topic.", exact=True)
                ).to_have_count(2)
                expect(
                    page.get_by_role("button", name="Confirm my answers")
                ).to_be_enabled()
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            page.screenshot(
                path=str(
                    screenshot_directory
                    / f"visit-prep-review-{width}{'-revealed' if initial_adherence == 'yes' else ''}.png"
                ),
                full_page=True,
            )
            page.get_by_role("button", name="Confirm my answers").click()
            expect(
                page.get_by_text("You confirmed these saved answers.", exact=False)
            ).to_be_visible()
            assert (
                len(correction_keys) == (2 if initial_adherence == "some" else 3)
                and correction_keys[0] == correction_keys[1]
            )
            assert correction_bodies[0] == correction_bodies[1]
            assert fixture.access_token not in page.content()
            stores = page.evaluate(
                "JSON.stringify([Object.entries(localStorage), Object.entries(sessionStorage)])"
            )
            assert (
                story not in stores
                and revised_story not in stores
                and fixture.access_token not in stores
            )
            page.evaluate(
                "window.dispatchEvent(new Event('cosmaneura:view-as-hard-block'))"
            )
            expect(page.get_by_text(revised_story, exact=True)).to_have_count(0)
            expect(page.get_by_text(story, exact=True)).to_have_count(0)
            expect(page.get_by_role("alert")).to_be_visible()
            assert len(response_keys) == (6 if initial_adherence == "some" else 5)
            assert len(set(response_keys)) == (5 if initial_adherence == "some" else 4)
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
        events = session.exec(
            select(VisitPrepContributionEvent)
            .where(VisitPrepContributionEvent.visit_prep_id == fixture.prep_id)
            .order_by(col(VisitPrepContributionEvent.event_sequence))
        ).all()
    assert len(responses) == (5 if initial_adherence == "some" else 4)
    story_response = next(
        row for row in responses if row.submission_sequence == len(responses)
    )
    assert story_response.answer_ciphertext is not None
    assert story not in story_response.answer_ciphertext
    assert story in decrypt_value(story_response.answer_ciphertext)
    assert [event.event_kind for event in events] == (
        ["correction", "review"]
        if initial_adherence == "some"
        else ["correction", "topic_answer", "review"]
    )
    assert events[0].answer_ciphertext is not None
    assert revised_story not in events[0].answer_ciphertext
    if initial_adherence == "some":
        assert revised_story in decrypt_value(events[0].answer_ciphertext)
    else:
        assert events[1].response_id is None and events[1].response_kind == "skipped"
        assert events[1].question_id is not None
