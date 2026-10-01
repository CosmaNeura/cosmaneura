"""Actual provider component and API on mobile/desktop, synthetic identity only.

Build web/scripts/build-visit-prep-package-harness.mjs and the test production
assets first. The harness replaces account authentication, not product UI or
backend authorization. Every request remains inside TestClient; no server,
external account, model, provider credential or patient record is used.
"""

from urllib.parse import urlsplit
from uuid import uuid4

import pytest
from playwright.sync_api import expect, sync_playwright

from app import root
from app.services.visit_prep_contributions import correct_contribution
from app.services.visit_prep_interview_contract import ResponseInput
from app.services.visit_prep_synthesis_worker import run_one_build

from tests.routes.visit_prep_question_sets_test import _provider_client
from tests.services.visit_prep_contribution_projection_test import (
    _prepared,
    _projection,
)
from tests.services.visit_prep_synthesis_publication_test import _rows, _selected
from tests.services.visit_prep_synthesis_test import _brief, _fake
from tests.services.visit_prep_synthesis_usage_test import _events


@pytest.mark.parametrize("width,height", [(1440, 1000), (390, 844)])
def test_provider_package_displays_current_patient_words_and_clears_on_denial(
    monkeypatch, width, height
):
    assets = root / "web/build/test/client/assets"
    styles = list(assets.glob("root-*.css"))
    harness = root / "web/build/test/visit-prep-package-harness.js"
    assert len(styles) == 1 and harness.is_file(), (
        "Build local synthetic package harness and production test assets first"
    )
    fixture = _prepared(monkeypatch, f"provider-browser-{width}", role="caregiver")
    correct_contribution(
        access_token=fixture.access_token,
        first_name="José",
        role="caregiver",
        expected_basis=_projection(fixture).basis,
        idempotency_key=str(uuid4()),
        response=ResponseInput(
            "story", "answered", "My revised synthetic pre-visit story."
        ),
    )
    selected = _selected(fixture)
    captured = _fake(monkeypatch, _brief(selected).model_dump_json())
    lose_ack = False
    api_paths = []
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

            def in_process(route):
                request = route.request
                url = urlsplit(request.url)
                assert url.scheme == "https" and url.netloc == "testserver"
                if url.path == "/synthetic-provider":
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
                    assert url.path.startswith(
                        "/internal/v1/visit-prep/preps"
                    ) and request.method in {"GET", "POST"}
                    api_paths.append(url.path)
                    response = client.request(
                        request.method,
                        url.path + ("?" + url.query if url.query else ""),
                        content=request.post_data,
                        headers={"Content-Type": "application/json"}
                        if request.method == "POST"
                        else {},
                    )
                    if request.method == "POST":
                        assert response.status_code == 202
                        assert response.headers["cache-control"] == "no-store"
                        assert set(response.json()) == {
                            "package_id",
                            "source_version",
                            "state",
                        }
                        if lose_ack:
                            assert run_one_build() == "ready"
                            route.abort("failed")
                            return
                    route.fulfill(
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
            page.goto("https://testserver/synthetic-provider")
            expect(
                page.get_by_role("heading", name="Review Visit Prep")
            ).to_be_visible()
            assert api_paths == []
            page.get_by_role("button", name="Load preparations").click()
            page.get_by_role(
                "button", name=f"Open contribution {fixture.prep_id}"
            ).click()
            expect(
                page.get_by_role("heading", name="Patient contribution")
            ).to_be_visible()
            expect(
                page.get_by_text("My revised synthetic pre-visit story.", exact=True)
            ).to_be_visible()
            expect(
                page.get_by_text("Caregiver report", exact=False).first
            ).to_be_visible()
            expect(
                page.get_by_text("AI synthesis has not been generated.", exact=False)
            ).to_be_visible()
            assert not captured and not _events(fixture)
            page.get_by_role("button", name="Generate synthesis", exact=True).click()
            expect(page.get_by_text("Synthesis queued.", exact=False)).to_be_visible()
            assert not captured and not _events(fixture)
            assert run_one_build() == "ready"
            page.get_by_role("button", name="Reload contribution").click()
            expect(
                page.get_by_text("AI interpretation of patient-reported", exact=False)
            ).to_be_visible()
            expect(
                page.get_by_text(
                    "Recipient reports: My revised synthetic pre-visit story.",
                    exact=True,
                )
            ).to_be_visible()
            page.locator("li > details").filter(
                has=page.locator("blockquote").filter(
                    has_text="My revised synthetic pre-visit story."
                )
            ).get_by_text("Supporting patient words", exact=True).click()
            expect(
                page.locator("blockquote").filter(
                    has_text="My revised synthetic pre-visit story."
                )
            ).to_be_visible()
            assert len(_rows(fixture)) == 1 and len(_events(fixture)) == 2
            page.get_by_text(
                "Recipient revised this response — original wording", exact=True
            ).click()
            expect(
                page.get_by_text("My invented health story.", exact=True)
            ).to_be_visible()
            assert page.evaluate(
                "document.documentElement.scrollWidth <= window.innerWidth"
            )
            assert fixture.access_token not in page.content()
            assert (
                page.evaluate(
                    "JSON.stringify([Object.entries(localStorage), Object.entries(sessionStorage)])"
                )
                == "[[],[]]"
            )
            page.screenshot(
                path=str(root / f"tmp/visit-prep-provider-{width}.png"), full_page=True
            )
            # Correction supersedes the old brief; a lost POST acknowledgement
            # must recover by GET, not an automatic second model request.
            correct_contribution(
                access_token=fixture.access_token,
                first_name="José",
                role="caregiver",
                expected_basis=_projection(fixture).basis,
                idempotency_key=str(uuid4()),
                response=ResponseInput(
                    "story", "answered", "My latest synthetic interval story."
                ),
            )
            page.get_by_role("button", name="Reload contribution").click()
            expect(
                page.get_by_text("The contribution changed.", exact=False)
            ).to_be_visible()
            expect(
                page.get_by_text(
                    "Recipient reports: My revised synthetic pre-visit story.",
                    exact=True,
                )
            ).to_have_count(0)
            current = _selected(fixture)
            _fake(monkeypatch, _brief(current).model_dump_json())
            lose_ack = True
            page.get_by_role("button", name="Generate synthesis", exact=True).click()
            expect(page.get_by_role("alert")).to_contain_text("status is unknown")
            expect(
                page.get_by_text("My latest synthetic interval story.", exact=True)
            ).to_have_count(0)
            page.get_by_role("button", name="Reload contribution").click()
            expect(
                page.get_by_text(
                    "Recipient reports: My latest synthetic interval story.", exact=True
                )
            ).to_be_visible()
            assert len(_rows(fixture)) == 2 and len(_events(fixture)) == 4
            assert run_one_build() == "idle"
            assert (
                api_paths.count(
                    f"/internal/v1/visit-prep/preps/{fixture.prep_id}/synthesis"
                )
                >= 6
            )
            fixture.practice.negotiated_baa_on_file = False
            fixture.practice.save()
            page.get_by_role("button", name="Reload contribution").click()
            expect(page.get_by_role("alert")).to_be_visible()
            expect(
                page.get_by_text("My revised synthetic pre-visit story.", exact=True)
            ).to_have_count(0)
            expect(
                page.get_by_text(
                    "Recipient reports: My latest synthetic interval story.", exact=True
                )
            ).to_have_count(0)
            expect(
                page.get_by_text("My invented health story.", exact=True)
            ).to_have_count(0)
            page.evaluate(
                "window.dispatchEvent(new Event('cosmaneura:view-as-hard-block'))"
            )
            expect(
                page.get_by_text("Patient contributions are unavailable", exact=False)
            ).to_be_visible()
            context.close()
        finally:
            browser.close()
