"""Real dashboard component and policy API; synthetic identity, no external I/O."""

from urllib.parse import urlsplit

import pytest
from playwright.sync_api import expect, sync_playwright

from app import root
from app.services import visit_prep_access
from app.services.visit_prep_email_policy import record_email_policy_decision

from activemodel.session_manager import get_session
from app.models.visit_prep_delivery import VisitPrepEmailDelivery
from app.models.visit_prep_email_authority import VisitPrepEmailConsentEvent
from sqlmodel import select

from tests.routes.visit_prep_question_sets_test import _provider_client
from tests.services.visit_prep_email_policy_test import _audits, _inputs, _ready, _rows

PATH = "/internal/v1/visit-prep/email-policy"


@pytest.mark.parametrize("width,height", [(1440, 1000), (390, 844)])
def test_policy_control_actual_choices_uncertain_ack_and_current_denial(
    monkeypatch, width, height
):
    fixture = _ready(f"policy-browser-{width}")
    scope, settings = fixture
    monkeypatch.setattr(visit_prep_access, "VISIT_PREP_SETTINGS", settings)
    styles = list((root / "web/build/test/client/assets").glob("root-*.css"))
    harness = root / "web/build/test/visit-prep-package-harness.js"
    assert len(styles) == 1 and harness.is_file()
    requests = []
    lose_ack = False
    with (
        _provider_client(scope.doctor, monkeypatch) as client,
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
                if url.path == "/synthetic-policy":
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
                    assert (
                        url.path == PATH
                        and not url.query
                        and request.method in {"GET", "POST"}
                    )
                    headers = {"Content-Type": "application/json"}
                    if request.method == "POST":
                        headers["Idempotency-Key"] = request.headers["idempotency-key"]
                    requests.append(request.method)
                    response = client.request(
                        request.method, PATH, content=request.post_data, headers=headers
                    )
                    assert response.headers["cache-control"] == "no-store"
                    if request.method == "POST" and lose_ack:
                        assert response.status_code == 200
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
            page.goto("https://testserver/synthetic-policy")
            expect(
                page.get_by_role("heading", name="Visit Prep invitation email policy")
            ).to_be_visible()
            assert requests == []
            page.get_by_role("button", name="Load / reload email policy").click()
            expect(
                page.get_by_text("No physician approval recorded", exact=True)
            ).to_be_visible()
            expect(
                page.get_by_text("Delivery is not connected.", exact=False)
            ).to_be_visible()
            expect(
                page.get_by_role("button", name="Record physician approval")
            ).to_be_disabled()
            page.get_by_label("Approval expires at (UTC)").fill("2099-01-01T12:00")
            page.get_by_role("checkbox").check()
            page.get_by_role("button", name="Record physician approval").click()
            expect(page.get_by_role("status")).to_contain_text("Decision recorded")
            expect(
                page.get_by_role("heading", name="Invitation preview")
            ).to_have_count(0)
            page.get_by_role("button", name="Load / reload email policy").click()
            expect(
                page.get_by_text("Physician policy approved at last load", exact=True)
            ).to_be_visible()
            # A decision committed elsewhere invalidates the UI's CAS sequence.
            record_email_policy_decision(
                **_inputs(
                    fixture,
                    expected_event_sequence=1,
                    event_kind="withdrawn",
                    expires_at=None,
                )
            )
            page.get_by_role("button", name="Withdraw physician approval").click()
            expect(page.get_by_role("status")).to_contain_text("outcome is unknown")
            page.get_by_role("button", name="Load / reload email policy").click()
            expect(
                page.get_by_text("Physician approval withdrawn", exact=True)
            ).to_be_visible()
            lose_ack = True
            page.get_by_label("Approval expires at (UTC)").fill("2099-01-02T12:00")
            page.get_by_role("checkbox").check()
            page.get_by_role("button", name="Record physician approval").click()
            expect(page.get_by_role("status")).to_contain_text("outcome is unknown")
            assert len(_rows(scope)) == len(_audits(scope)) == 3
            lose_ack = False
            page.get_by_role("button", name="Load / reload email policy").click()
            expect(
                page.get_by_text("Physician policy approved at last load", exact=True)
            ).to_be_visible()
            assert requests.count("POST") == 3
            assert page.evaluate(
                "document.documentElement.scrollWidth <= window.innerWidth"
            )
            assert "vp1_" not in page.content() and "ciphertext" not in page.content()
            assert (
                page.evaluate(
                    "JSON.stringify([Object.entries(localStorage),Object.entries(sessionStorage)])"
                )
                == "[[],[]]"
            )
            page.screenshot(
                path=str(root / f"tmp/visit-prep-email-policy-{width}.png"),
                full_page=True,
            )
            scope.practice.negotiated_baa_on_file = False
            scope.practice.save()
            page.get_by_role("button", name="Load / reload email policy").click()
            expect(page.get_by_role("status")).to_contain_text("could not be loaded")
            expect(
                page.get_by_role("heading", name="Invitation preview")
            ).to_have_count(0)
            page.evaluate(
                "window.dispatchEvent(new Event('cosmaneura:view-as-hard-block'))"
            )
            expect(
                page.get_by_role("heading", name="Visit Prep invitation email policy")
            ).to_have_count(0)
        finally:
            browser.close()
    with get_session() as session:
        for model in (VisitPrepEmailConsentEvent, VisitPrepEmailDelivery):
            assert not session.exec(
                select(model).where(model.owner_practice_member_id == scope.owner.id)
            ).all()
