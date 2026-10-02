"""Real SMS policy component and owner API, desktop/mobile synthetic acceptance."""

from dataclasses import replace
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import expect, sync_playwright

from app import root
from app.configuration import visit_prep_sms

from activemodel.session_manager import get_engine
from sqlalchemy import text
from sqlmodel import Session

from tests.integration.visit_prep_sms_policy_http_test import PATH, _ready
from tests.integration.visit_prep_sms_policy_test import _audits, _rows
from tests.routes.visit_prep_question_sets_test import _provider_client


@pytest.mark.parametrize("width,height", [(1440, 1000), (390, 844)])
def test_sms_policy_dashboard_real_decisions_stale_sender_lost_ack_and_denial(
    monkeypatch, width, height
):
    fixture, settings, routing = _ready(monkeypatch)
    styles = list((root / "web/build/test/client/assets").glob("root-*.css"))
    harness = root / "web/build/test/visit-prep-package-harness.js"
    assert len(styles) == 1 and harness.is_file()
    requests, errors, lose_ack = [], [], False
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
                request, url = route.request, urlsplit(route.request.url)
                assert url.scheme == "https" and url.netloc == "testserver"
                if url.path == "/synthetic-sms-policy":
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
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto("https://testserver/synthetic-sms-policy")
            panel = page.get_by_role("region", name="Visit Prep SMS policy")
            expect(panel).to_be_visible()
            assert requests == []
            panel.get_by_role("button", name="Load / reload SMS policy").click()
            expect(
                panel.get_by_text("No physician SMS approval recorded", exact=True)
            ).to_be_visible()
            expect(
                panel.get_by_text("SMS delivery is not connected.", exact=False)
            ).to_be_visible()
            expect(
                panel.get_by_role("heading", name="48-hour unopened reminder preview")
            ).to_be_visible()
            expect(
                panel.get_by_role("heading", name="12-hour unfinished reminder preview")
            ).to_be_visible()
            expect(
                panel.get_by_role("button", name="Record physician SMS approval")
            ).to_be_disabled()
            panel.get_by_label("SMS approval expires at (UTC)").fill("2099-01-01T12:00")
            panel.get_by_role("checkbox").check()
            panel.get_by_role("button", name="Record physician SMS approval").click()
            expect(panel.get_by_role("status")).to_contain_text("No text was sent")
            panel.get_by_role("button", name="Load / reload SMS policy").click()
            expect(
                panel.get_by_text(
                    "Physician SMS policy approved at last load", exact=True
                )
            ).to_be_visible()
            # A new sender invalidates the tab's reviewed sender binding.
            replacement = replace(routing, sender_number="+12025550101")
            monkeypatch.setattr(visit_prep_sms, "ROUTING", replacement)
            panel.get_by_label("SMS approval expires at (UTC)").fill("2099-01-02T12:00")
            panel.get_by_role("checkbox").check()
            panel.get_by_role("button", name="Record physician SMS approval").click()
            expect(panel.get_by_role("status")).to_contain_text("outcome is unknown")
            assert len(_rows(fixture)) == 1
            panel.get_by_role("button", name="Load / reload SMS policy").click()
            expect(
                panel.get_by_text(
                    "SMS sender changed — new approval required", exact=True
                )
            ).to_be_visible()
            panel.get_by_role("button", name="Withdraw physician SMS approval").click()
            expect(panel.get_by_role("status")).to_contain_text("Decision recorded")
            panel.get_by_role("button", name="Load / reload SMS policy").click()
            expect(
                panel.get_by_text("Physician SMS approval withdrawn", exact=True)
            ).to_be_visible()
            lose_ack = True
            panel.get_by_label("SMS approval expires at (UTC)").fill("2099-01-02T12:00")
            panel.get_by_role("checkbox").check()
            panel.get_by_role("button", name="Record physician SMS approval").click()
            expect(panel.get_by_role("status")).to_contain_text("outcome is unknown")
            assert len(_rows(fixture)) == len(_audits(fixture)) == 3
            lose_ack = False
            panel.get_by_role("button", name="Load / reload SMS policy").click()
            expect(
                panel.get_by_text(
                    "Physician SMS policy approved at last load", exact=True
                )
            ).to_be_visible()
            assert requests.count("POST") == 4
            assert "vp1_" not in page.content() and "ciphertext" not in page.content()
            assert (
                page.evaluate(
                    "JSON.stringify([Object.entries(localStorage),Object.entries(sessionStorage)])"
                )
                == "[[],[]]"
            )
            assert page.evaluate(
                "document.documentElement.scrollWidth <= window.innerWidth"
            )
            assert errors == []
            page.screenshot(
                path=str(root / f"tmp/visit-prep-sms-policy-{width}.png"),
                full_page=True,
            )
            with Session(get_engine()) as session:
                session.execute(
                    text(
                        "UPDATE practice SET negotiated_baa_on_file=false WHERE id=:id"
                    ),
                    {"id": fixture.practice.id.uuid},
                )
                session.commit()
            panel.get_by_role("button", name="Load / reload SMS policy").click()
            expect(panel.get_by_role("status")).to_contain_text("could not be loaded")
            expect(
                panel.get_by_role("heading", name="Booking invitation preview")
            ).to_have_count(0)
            page.evaluate(
                "window.dispatchEvent(new Event('cosmaneura:view-as-hard-block'))"
            )
            expect(
                page.get_by_role("region", name="Visit Prep SMS policy")
            ).to_have_count(0)
        finally:
            browser.close()
    with Session(get_engine()) as session:
        for table in ("visit_prep_sms_consent_event", "visit_prep_sms_delivery"):
            assert (
                session.execute(
                    text(
                        f"SELECT count(*) FROM {table} WHERE owner_practice_member_id=:owner"
                    ),
                    {"owner": fixture.owner.id.uuid},
                ).scalar_one()
                == 0
            )
