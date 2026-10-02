"""Built patient help action through Core API/storage; invented AI transport."""

from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient
from playwright.sync_api import expect, sync_playwright

from app import root
from app.routes import visit_prep_interviews as route
from app.server import api_app

from tests.integration.visit_prep_patient_analysis_test import _enable
from tests.services.visit_prep_contribution_commands_test import _correct
from tests.services.visit_prep_contribution_projection_test import (
    _prepared,
    _projection,
    _topic,
)


@pytest.mark.parametrize("width,height", [(1440, 1000), (390, 844)])
def test_patient_requests_reviews_and_accepts_suggestion(
    monkeypatch, owned_rate_limit_redis, width, height
):
    fixture = _prepared(monkeypatch, "patient-ai-browser")
    _correct(
        fixture, answer="Fictional_Name_Alpha: my invented sleep concern comes first."
    )
    calls = _enable(monkeypatch)
    monkeypatch.setattr(
        route.rate_limits, "get_rate_limit_redis", lambda: owned_rate_limit_redis
    )
    client = TestClient(
        api_app,
        base_url="https://testserver",
        client=(f"198.19.1.{10 + (width == 390)}", 50000),
    )
    words = "my invented sleep concern comes first."
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
                    browser_route.abort()
                    return
                response = client.request(
                    request.method,
                    url.path,
                    headers={
                        key: value
                        for key, value in request.all_headers().items()
                        if key not in {"host", "content-length", "accept-encoding"}
                    },
                    content=request.post_data_buffer,
                )
                browser_route.fulfill(
                    status=response.status_code,
                    headers={
                        key: value
                        for key, value in response.headers.items()
                        if key not in {"content-length", "content-encoding"}
                    },
                    body=response.content,
                )

            context.route("**/*", dispatch)
            page = context.new_page()
            page.goto(
                f"https://testserver/visit-prep#access_token={fixture.access_token}"
            )
            page.get_by_label("Recipient first name").fill("José")
            page.get_by_role("button", name="Begin Visit Prep").click()
            page.get_by_role("button", name="Review or change saved answers").click()
            expect(
                page.get_by_role("heading", name="Your saved answers")
            ).to_be_visible()
            page.get_by_role(
                "button",
                name="Help me clarify: Tell me your health story — whatever feels relevant.",
                exact=True,
            ).click()
            expect(
                page.get_by_role("heading", name="Review proposed wording")
            ).to_be_visible()
            expect(page.get_by_text(words, exact=True)).to_be_visible()
            assert (
                len(calls) == 1
                and _topic(_projection(fixture), "story").answer != words
            )
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            (root / "tmp").mkdir(exist_ok=True)
            page.screenshot(
                path=str(root / "tmp" / f"visit-prep-patient-ai-{width}.png"),
                full_page=True,
            )
            page.get_by_role(
                "button", name="Accept proposed wording", exact=True
            ).click()
            expect(
                page.get_by_role("heading", name="Your saved answers")
            ).to_be_visible()
            expect(page.get_by_text(words, exact=True)).to_be_visible()
            assert _topic(_projection(fixture), "story").answer == words
            assert fixture.access_token not in page.content()
        finally:
            browser.close()
