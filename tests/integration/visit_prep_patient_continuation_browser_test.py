"""Actual desktop/mobile components with real cookie persistence and owned API.

All requests are intercepted; no server, live credentials or external model.
The support-view subscription is stubbed inactive in the synthetic bundle only.
"""

from urllib.parse import urlsplit
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from playwright.sync_api import expect, sync_playwright

import app.routes.visit_prep_interviews as routes
from app import root
from app.server import api_app

from tests.services.visit_prep_contribution_projection_test import _projection, _topic
from tests.services.visit_prep_interviews_test import _fixture


@pytest.mark.parametrize("width,height", [(1440, 1000), (390, 844)])
@pytest.mark.parametrize("role", ["patient", "caregiver"])
def test_initial_check_then_reload_reopen_and_edit_without_identity_prompts(
    monkeypatch, owned_rate_limit_redis, width, height, role
):
    bundle = root / "web/build/test/client/assets/visit-prep-patient-harness.js"
    assert bundle.is_file(), "Build the synthetic patient harness first"
    styles = sorted((root / "web/build/test/client/assets").glob("root-*.css"))
    assert styles
    fixture = _fixture(monkeypatch, "continuation-browser", recipient_role=role)
    monkeypatch.setattr(
        routes.rate_limits, "get_rate_limit_redis", lambda: owned_rate_limit_redis
    )
    peer = f"2001:db8::{uuid4().hex[:4]}:{uuid4().hex[:4]}"
    client = TestClient(api_app, base_url="https://testserver", client=(peer, 50000))
    api_calls = []
    unexpected = []
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

            def dispatch(browser_route):
                request = browser_route.request
                url = urlsplit(request.url)
                if url.scheme != "https" or url.netloc != "testserver":
                    unexpected.append(request.url)
                    browser_route.abort()
                    return
                assert not url.query and not url.fragment
                if url.path == "/visit-prep":
                    browser_route.fulfill(
                        status=200,
                        content_type="text/html",
                        body=f'<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/assets/{styles[0].name}"></head><body><div id="root"></div><script type="module" src="/assets/{bundle.name}"></script></body></html>',
                    )
                    return
                headers = request.all_headers()
                if url.path.startswith("/external/v1/visit-prep"):
                    assert headers["x-visit-prep-continuation"] == "1"
                    assert not any(
                        k in headers for k in ("referer", "sentry-trace", "baggage")
                    )
                    api_calls.append(
                        (
                            url.path,
                            request.post_data_json if request.post_data else None,
                        )
                    )
                    # The browser, not TestClient's jar, must supply the remembered cookie.
                    client.cookies.clear()
                response = client.request(
                    request.method,
                    url.path,
                    headers={
                        k: v
                        for k, v in headers.items()
                        if k not in {"host", "content-length", "accept-encoding"}
                    },
                    content=request.post_data_buffer,
                )
                assert response.status_code not in {429, 503}, (
                    url.path,
                    response.status_code,
                )
                browser_route.fulfill(
                    status=response.status_code,
                    headers={
                        k: v
                        for k, v in response.headers.items()
                        if k not in {"content-length", "content-encoding"}
                    },
                    body=response.content,
                )

            context.route("**/*", dispatch)
            page = context.new_page()
            page.goto(
                f"https://testserver/visit-prep#access_token={fixture.access_token}"
            )
            expect(page.get_by_role("heading", name="Before we begin")).to_be_visible()
            page.get_by_label("Recipient first name").fill("José")
            page.get_by_label("This invitation was sent to", exact=True).select_option(
                role
            )
            page.get_by_role("button", name="Begin Visit Prep").click()
            expect(
                page.get_by_label("What is the main reason for this visit?")
            ).to_be_visible()
            page.reload()
            expect(
                page.get_by_label("What is the main reason for this visit?")
            ).to_be_visible()
            expect(page.get_by_label("Recipient first name")).to_have_count(0)
            page.get_by_label("What is the main reason for this visit?").fill(
                "My original invented reason."
            )
            page.get_by_role("button", name="Save and continue").click()
            page.get_by_role("button", name="Skip this topic").click()
            page.get_by_role("button", name="Yes, as prescribed", exact=False).click()
            story_prompt = "Tell me your health story — whatever feels relevant."
            page.get_by_label(story_prompt).fill("My original invented story.")
            page.get_by_role("button", name="Save and continue").click()
            page.get_by_role("button", name="Finish Visit Prep", exact=True).click()
            expect(
                page.get_by_role("heading", name="Visit Prep complete")
            ).to_be_visible()
            # Reopen the invitation in a new document, as from email/SMS;
            # changing only the fragment on the same document is not a reopen.
            page.goto("about:blank")
            page.goto(
                f"https://testserver/visit-prep#access_token={fixture.access_token}"
            )
            expect(
                page.get_by_role("heading", name="Visit Prep complete")
            ).to_be_visible()
            page.get_by_role("button", name="Review or change saved answers").click()
            expect(
                page.get_by_role("heading", name="Your saved answers")
            ).to_be_visible()
            expect(page.get_by_label("Recipient first name")).to_have_count(0)
            adherence_prompt = "Are you taking them as prescribed?"
            page.get_by_role(
                "button", name=f"Explain: {adherence_prompt}", exact=True
            ).click()
            page.get_by_label("What would you like your provider to understand?").fill(
                "My invented explanation for this choice."
            )
            page.get_by_role("button", name="Save my explanation", exact=True).click()
            expect(
                page.get_by_text("My invented explanation for this choice.", exact=True)
            ).to_be_visible()
            explanation = _topic(_projection(fixture), "adherence")
            assert explanation.answer == explanation.original_answer == "yes"
            assert (
                explanation.explanation_words
                == "My invented explanation for this choice."
            )
            # Another isolated browser suite may steal foreground focus. The
            # actual privacy handler must conceal on blur; explicitly reload
            # authorized content before capturing the explanation view.
            page.bring_to_front()
            show_saved = page.get_by_role(
                "button", name="Show saved answers", exact=True
            )
            if show_saved.count():
                show_saved.click()
            expect(
                page.get_by_text("My invented explanation for this choice.", exact=True)
            ).to_be_visible()
            page.screenshot(
                path=str(root / "tmp" / f"visit-prep-explanation-{width}-{role}.png"),
                full_page=True,
            )
            page.get_by_role(
                "button", name=f"Change: {story_prompt}", exact=True
            ).click()
            page.get_by_label(story_prompt).fill("My updated invented story.")
            page.get_by_role("button", name="Save and continue", exact=True).click()
            expect(
                page.get_by_role("heading", name="Your saved answers")
            ).to_be_visible()
            page.get_by_role("button", name="Confirm my answers", exact=True).click()
            expect(
                page.get_by_text("You confirmed these saved answers.", exact=False)
            ).to_be_visible()
            # Email clients can target the current tab without a document reload.
            page.goto(
                f"https://testserver/visit-prep#access_token={fixture.access_token}"
            )
            expect(page).to_have_url("https://testserver/visit-prep")
            expect(
                page.get_by_role("heading", name="Visit Prep complete")
            ).to_be_visible()
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            (root / "tmp").mkdir(exist_ok=True)
            page.screenshot(
                path=str(root / "tmp" / f"visit-prep-continuation-{width}-{role}.png"),
                full_page=True,
            )
            assert sum(path.endswith("/confirm") for path, _ in api_calls) == 1
            for path, body in api_calls:
                if body and not path.endswith("/confirm"):
                    assert "first_name" not in body
            assert (
                page.evaluate(
                    "Object.keys(localStorage).length + Object.keys(sessionStorage).length"
                )
                == 0
            )
            assert fixture.access_token not in page.url
            assert not unexpected
            story = _topic(_projection(fixture), "story")
            assert story.answer == "My updated invented story."
            assert story.original_answer == "My original invented story."
        finally:
            browser.close()
