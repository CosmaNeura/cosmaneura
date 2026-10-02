"""Actual dashboard and current-owner API; isolated browser, synthetic transport."""

from urllib.parse import urlsplit

import pytest
from playwright.sync_api import expect, sync_playwright

from app import root
from app.services import visit_prep_sms_outbox as outbox
from app.services import visit_prep_sms_worker as worker

from tests.integration.visit_prep_sms_outbox_test import (
    _ready,
    _row,
)  # noqa: F401
from tests.integration.visit_prep_sms_status_test import _audits
from tests.integration.visit_prep_sms_worker_test import _worker
from tests.routes.visit_prep_question_sets_test import _provider_client


@pytest.mark.parametrize("width,height", [(1440, 1000), (390, 844)])
def test_sms_dashboard_snapshot_tracks_real_worker_without_browser_sends(
    monkeypatch, width, height
):
    fixture, options, _ = _ready(monkeypatch)
    receipt = outbox.enqueue_sms(**options)
    harness = root / "web/build/test/visit-prep-package-harness.js"
    styles = list((root / "web/build/test/client/assets").glob("root-*.css"))
    assert len(styles) == 1 and harness.is_file()
    requests, errors = [], []
    path = f"/internal/v1/visit-prep/preps/{fixture.prep_id}/sms-status"
    with (
        _provider_client(fixture.doctor, monkeypatch) as client,
        sync_playwright() as playwright,
    ):
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

            def local(route):
                request, url = route.request, urlsplit(route.request.url)
                assert url.scheme == "https" and url.netloc == "testserver"
                if url.path == "/synthetic-status":
                    route.fulfill(
                        content_type="text/html",
                        body='<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/synthetic.css"></head><body><main id="root" class="mx-auto max-w-3xl p-4"></main><script type="module" src="/synthetic.js"></script></body></html>',
                    )
                elif url.path == "/synthetic.js":
                    route.fulfill(
                        content_type="text/javascript", body=harness.read_bytes()
                    )
                elif url.path == "/synthetic.css":
                    route.fulfill(content_type="text/css", body=styles[0].read_bytes())
                else:
                    assert request.method == "GET" and url.path in {
                        path,
                        "/internal/v1/visit-prep/preps",
                    }
                    requests.append(url.path)
                    response = client.get(
                        url.path + ("?" + url.query if url.query else "")
                    )
                    assert response.headers["cache-control"] == "no-store"
                    route.fulfill(
                        status=response.status_code,
                        headers={
                            k: v
                            for k, v in response.headers.items()
                            if k not in {"content-length", "content-encoding"}
                        },
                        body=response.content,
                    )

            context.route("**/*", local)
            page = context.new_page()
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto("https://testserver/synthetic-status")
            assert requests == []
            page.get_by_role("button", name="Load preparations").click()
            page.get_by_role(
                "button", name=f"SMS invitation and reminder status {fixture.prep_id}"
            ).click()
            expect(
                page.get_by_role("heading", name="Invitation and reminder SMS status")
            ).to_be_visible()
            assert requests == ["/internal/v1/visit-prep/preps"]
            page.get_by_role("button", name="Load / reload SMS status").click()
            expect(
                page.get_by_text("Text queued at last load", exact=True)
            ).to_be_visible()
            expect(page.get_by_text("No text requested", exact=True)).to_have_count(2)
            assert _audits(fixture) == 1 and _row(receipt).state == "queued"
            calls = []

            def transport(message):
                calls.append(message)
                return "accepted"

            assert (
                worker.run_one_sms(**_worker(options, transport=transport))
                == "accepted"
            )
            expect(
                page.get_by_text("Text queued at last load", exact=True)
            ).to_be_visible()
            assert requests == ["/internal/v1/visit-prep/preps", path]
            page.get_by_role("button", name="Load / reload SMS status").click()
            expect(
                page.get_by_text("SMS service accepted the text", exact=True)
            ).to_be_visible()
            expect(
                page.get_by_text(
                    "Service acceptance does not prove the patient received or opened a text.",
                    exact=True,
                )
            ).to_be_visible()
            assert len(calls) == 1 and _audits(fixture) == 2
            assert all(
                secret not in page.content()
                for secret in (
                    fixture.access_token,
                    "+12025550123",
                    "patient@example.com",
                )
            )
            assert (
                page.evaluate(
                    "JSON.stringify([Object.entries(localStorage),Object.entries(sessionStorage)])"
                )
                == "[[],[]]"
            )
            assert page.evaluate(
                "document.documentElement.scrollWidth <= window.innerWidth"
            )
            page.screenshot(
                path=str(root / f"tmp/visit-prep-sms-status-{width}.png"),
                full_page=True,
            )
            fixture.practice.negotiated_baa_on_file = False
            fixture.practice.save()
            page.get_by_role("button", name="Load / reload SMS status").click()
            expect(page.get_by_role("alert")).to_contain_text(
                "SMS status is unavailable"
            )
            expect(
                page.get_by_text("SMS service accepted the text", exact=True)
            ).to_have_count(0)
            page.evaluate(
                "window.dispatchEvent(new Event('cosmaneura:view-as-hard-block'))"
            )
            expect(
                page.get_by_role("heading", name="Invitation and reminder SMS status")
            ).to_have_count(0)
            assert not errors and len(calls) == 1
        finally:
            browser.close()
