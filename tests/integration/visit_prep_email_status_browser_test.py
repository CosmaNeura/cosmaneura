"""Built dashboard status plus real API and fake transport, synthetic data only."""

from urllib.parse import urlsplit

import pytest
from playwright.sync_api import expect, sync_playwright

from app import root
from app.services import visit_prep_access
from app.services.visit_prep_email_worker import run_one_email

from tests.routes.visit_prep_question_sets_test import _provider_client
from tests.services.visit_prep_email_enqueue_test import _rows
from tests.services.visit_prep_email_status_test import _audits
from tests.services.visit_prep_email_worker_test import _options, _queued


@pytest.mark.parametrize("width,height", [(1440, 1000), (390, 844)])
def test_dashboard_reads_actual_invitation_before_interview_without_resending(
    monkeypatch, width, height
):
    fixture = _queued(f"status-browser-{width}")
    monkeypatch.setattr(
        visit_prep_access, "VISIT_PREP_SETTINGS", fixture[3]["settings"]
    )
    styles = list((root / "web/build/test/client/assets").glob("root-*.css"))
    harness = root / "web/build/test/visit-prep-package-harness.js"
    assert len(styles) == 1 and harness.is_file()
    requests = []
    path = f"/internal/v1/visit-prep/preps/{fixture[1].id}/email-status"
    with (
        _provider_client(fixture[0].doctor, monkeypatch) as client,
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
                request = route.request
                url = urlsplit(request.url)
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
            page.goto("https://testserver/synthetic-status")
            assert requests == []
            page.get_by_role("button", name="Load preparations").click()
            page.get_by_role(
                "button", name=f"Email invitation status {fixture[1].id}"
            ).click()
            expect(
                page.get_by_role("heading", name="Invitation email status")
            ).to_be_visible()
            assert requests == ["/internal/v1/visit-prep/preps"]
            expect(
                page.get_by_role("button", name=f"Open contribution {fixture[1].id}")
            ).to_be_disabled()
            page.get_by_role("button", name="Load / reload invitation status").click()
            expect(
                page.get_by_text("Invitation queued at last load", exact=True)
            ).to_be_visible()
            assert len(_audits(fixture)) == 1 and _rows(fixture)[0].state == "queued"
            calls = []

            def transport(invitation):
                calls.append(invitation)
                return "accepted"

            assert run_one_email(**_options(fixture), transport=transport) == "accepted"
            # No optimistic update or polling: current snapshot remains queued.
            expect(
                page.get_by_text("Invitation queued at last load", exact=True)
            ).to_be_visible()
            page.get_by_role("button", name="Load / reload invitation status").click()
            expect(
                page.get_by_text("Email provider accepted the invitation", exact=True)
            ).to_be_visible()
            expect(
                page.get_by_text(
                    "Provider acceptance does not prove the patient received or opened the email.",
                    exact=True,
                )
            ).to_be_visible()
            assert len(calls) == 1 and len(_audits(fixture)) == 2
            assert (
                fixture[3]["access_token"] not in page.content()
                and "patient@example.com" not in page.content()
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
                path=str(root / f"tmp/visit-prep-email-status-{width}.png"),
                full_page=True,
            )
            fixture[0].practice.negotiated_baa_on_file = False
            fixture[0].practice.save()
            page.get_by_role("button", name="Load / reload invitation status").click()
            expect(page.get_by_role("alert")).to_contain_text(
                "Invitation status is unavailable"
            )
            expect(
                page.get_by_text("Email provider accepted the invitation", exact=True)
            ).to_have_count(0)
            page.evaluate(
                "window.dispatchEvent(new Event('cosmaneura:view-as-hard-block'))"
            )
            expect(
                page.get_by_role("heading", name="Invitation email status")
            ).to_have_count(0)
            assert len(calls) == 1
        finally:
            browser.close()
