"""Actual optional SMS screen and browser-held confirmation, synthetic only."""

from dataclasses import replace
from urllib.parse import urlsplit
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from playwright.sync_api import expect, sync_playwright

from app import root
from app.routes import visit_prep_interviews as routes
from app.server import api_app
from app.services import visit_prep_access

from tests.integration.visit_prep_sms_consent_test import _rows
from tests.integration.visit_prep_sms_suppression_test import _event, _record, _routing
from tests.services.visit_prep_interviews_test import _fixture


@pytest.mark.parametrize(
    "width,role,stopped",
    [
        (1440, "patient", False),
        (390, "caregiver", False),
        (1440, "patient", True),
        (390, "caregiver", True),
    ],
)
def test_actual_sms_choice_does_not_reconfirm_identity_or_block_answers(
    monkeypatch, owned_rate_limit_redis, width, role, stopped
):
    stop_event = _event(_routing(monkeypatch)) if stopped else None
    fixture = _fixture(
        monkeypatch, "sms-browser", recipient_role=role, phone="+12025550123"
    )
    monkeypatch.setattr(
        visit_prep_access,
        "VISIT_PREP_SETTINGS",
        replace(
            visit_prep_access.VISIT_PREP_SETTINGS, sms_consent_capture_enabled=True
        ),
    )
    monkeypatch.setattr(
        routes.rate_limits, "get_rate_limit_redis", lambda: owned_rate_limit_redis
    )
    bundle = root / "web/build/test/client/assets/visit-prep-patient-harness.js"
    style = sorted((root / "web/build/test/client/assets").glob("root-*.css"))[0]
    assert bundle.is_file()
    client = TestClient(
        api_app,
        base_url="https://testserver",
        client=(f"2001:db8::{uuid4().hex[:4]}:{uuid4().hex[:4]}", 50000),
    )
    calls, unexpected = [], []
    lost = False
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
                viewport={"width": width, "height": 1000 if width == 1440 else 844},
                service_workers="block",
            )

            def dispatch(browser_route):
                nonlocal lost
                request = browser_route.request
                url = urlsplit(request.url)
                if url.scheme != "https" or url.netloc != "testserver" or url.query:
                    unexpected.append(request.url)
                    browser_route.abort()
                    return
                if url.path == "/visit-prep":
                    browser_route.fulfill(
                        status=200,
                        content_type="text/html",
                        body=f'<html><head><link rel="stylesheet" href="/assets/{style.name}"></head><body><div id="root"></div><script type="module" src="/assets/{bundle.name}"></script></body></html>',
                    )
                    return
                headers = request.all_headers()
                if url.path.startswith("/external/v1/visit-prep"):
                    calls.append(
                        (
                            url.path,
                            request.post_data_json if request.post_data else None,
                        )
                    )
                    assert headers["x-visit-prep-continuation"] == "1"
                    assert not any(
                        k in headers for k in ("referer", "sentry-trace", "baggage")
                    )
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
                if url.path.endswith("/sms-consent/decision") and not lost:
                    assert response.status_code == 200
                    lost = True
                    browser_route.fulfill(
                        status=503, body="Synthetic lost acknowledgement"
                    )
                    return
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
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(
                f"https://testserver/visit-prep#access_token={fixture.access_token}"
            )
            page.get_by_label("Recipient first name").fill("José")
            page.get_by_label("This invitation was sent to", exact=True).select_option(
                role
            )
            page.get_by_role("button", name="Begin Visit Prep").click()
            page.get_by_role("button", name="Optional SMS choices", exact=True).click()
            expect(
                page.get_by_text("No SMS choice recorded at last load", exact=True)
            ).to_be_visible()
            expect(page.get_by_label("Recipient first name")).to_have_count(0)
            page.get_by_label("I reviewed these disclosures", exact=False).check()
            if role == "caregiver":
                page.get_by_label(
                    "I am authorized to make this communication choice", exact=False
                ).check()
            page.screenshot(
                path=str(root / "tmp" / f"visit-prep-sms-choice-{width}.png"),
                full_page=True,
            )
            page.get_by_role(
                "button", name="Record permission choice", exact=True
            ).click()
            expect(
                page.get_by_text("We could not confirm the SMS choice", exact=False)
            ).to_be_visible()
            assert len(_rows(fixture)) == 1
            assert (
                len(
                    [
                        path
                        for path, _ in calls
                        if path.endswith("/sms-consent/decision")
                    ]
                )
                == 1
            )
            page.get_by_role(
                "button", name="Load current SMS choices", exact=True
            ).click()
            expect(
                page.get_by_text("Permission choice recorded at last load", exact=False)
            ).to_be_visible()
            expect(page.get_by_label("Recipient first name")).to_have_count(0)
            if stop_event is not None:
                _record(stop_event)
                page.get_by_role(
                    "button", name="Back to preparation", exact=True
                ).click()
                page.get_by_role(
                    "button", name="Optional SMS choices", exact=True
                ).click()
                expect(
                    page.get_by_text(
                        "A phone-wide text opt-out is recorded", exact=False
                    )
                ).to_be_visible()
                expect(
                    page.get_by_role(
                        "button", name="Record permission choice", exact=True
                    )
                ).to_have_count(0)
                expect(
                    page.get_by_label("I reviewed these disclosures", exact=False)
                ).to_have_count(0)
                expect(page.get_by_label("Recipient first name")).to_have_count(0)
                page.screenshot(
                    path=str(root / "tmp" / f"visit-prep-sms-stop-{width}.png"),
                    full_page=True,
                )
                assert page.evaluate(
                    "document.documentElement.scrollWidth <= window.innerWidth"
                )
            page.get_by_role(
                "button", name="Do not allow / withdraw SMS texts", exact=True
            ).click()
            expect(
                page.get_by_text("Your decision was recorded", exact=False)
            ).to_be_visible()
            page.get_by_role("button", name="Back to preparation", exact=True).click()
            expect(
                page.get_by_label("What is the main reason for this visit?")
            ).to_be_visible()
            page.get_by_label("What is the main reason for this visit?").fill(
                "My invented patient priority."
            )
            page.get_by_role("button", name="Save and continue", exact=True).click()
            expect(
                page.get_by_label("What medications are you prescribed right now?")
            ).to_be_visible()
            assert [row.event_kind for row in _rows(fixture)] == [
                "granted",
                "withdrawn",
            ]
            assert len([path for path, _ in calls if path.endswith("/confirm")]) == 1
            assert all(
                "first_name" not in data
                for path, data in calls
                if "/sms-consent" in path
            )
            assert not any("email-consent" in path for path, _ in calls)
            assert not unexpected and not errors
            assert page.evaluate(
                "document.documentElement.scrollWidth <= window.innerWidth"
            )
        finally:
            browser.close()
