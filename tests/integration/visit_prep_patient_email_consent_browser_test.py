"""Real built patient choices over owned PG/Redis; no external message or model."""

from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient
from playwright.sync_api import expect, sync_playwright

import app.routes.visit_prep_interviews as route
from app import root
from app.server import api_app
from app.services.visit_prep_email_evidence import read_email_consent

from activemodel.session_manager import get_session
from app.models.visit_prep import VisitPrep
from app.models.visit_prep_delivery import VisitPrepEmailDelivery
from sqlmodel import select

from tests.services.visit_prep_email_consent_test import _rows
from tests.services.visit_prep_email_consent_view_test import _ready


@pytest.mark.parametrize("width,height", [(1440, 1000), (390, 844)])
@pytest.mark.parametrize("role", ["patient", "caregiver"])
def test_built_optional_choice_grant_uncertainty_completion_and_withdrawal(
    monkeypatch, owned_rate_limit_redis, width, height, role
):
    assert (root / "web/build/test/client/index.html").is_file()
    fixture = _ready(
        monkeypatch, f"email-screen-{width}-{role}", role=role, confirmed=False
    )
    monkeypatch.setattr(
        route.rate_limits, "get_rate_limit_redis", lambda: owned_rate_limit_redis
    )
    client = TestClient(
        api_app,
        base_url="https://testserver",
        client=(f"192.0.2.{240 + (width == 390) + 2 * (role == 'caregiver')}", 50000),
    )
    decisions: list[tuple[str, bytes]] = []
    reads = 0
    lost_ack = False
    forbidden: list[str] = []
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
                nonlocal lost_ack, reads
                request = browser_route.request
                url = urlsplit(request.url)
                if url.scheme != "https" or url.netloc != "testserver":
                    forbidden.append(request.url)
                    browser_route.abort()
                    return
                assert not url.query and not url.fragment
                path = url.path
                assert path == "/visit-prep" or path.startswith(
                    ("/assets/", "/cosmas", "/external/v1/visit-prep")
                )
                headers = request.all_headers()
                if path.startswith("/external/v1/visit-prep"):
                    assert headers["authorization"] == f"Bearer {fixture.access_token}"
                    assert not any(
                        name in headers
                        for name in ("referer", "sentry-trace", "baggage")
                    )
                    if path.endswith("/email-consent"):
                        reads += 1
                    if path.endswith("/email-consent/decision"):
                        assert request.post_data_buffer is not None
                        decisions.append(
                            (headers["idempotency-key"], request.post_data_buffer)
                        )
                response = client.request(
                    request.method,
                    path,
                    headers={
                        k: v
                        for k, v in headers.items()
                        if k not in {"host", "content-length", "accept-encoding"}
                    },
                    content=request.post_data_buffer,
                )
                if path.endswith("/email-consent/decision") and not lost_ack:
                    assert response.status_code == 200
                    lost_ack = True
                    browser_route.fulfill(
                        status=503, body="Synthetic lost acknowledgment"
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
            page.goto(
                f"https://testserver/visit-prep#access_token={fixture.access_token}"
            )
            expect(page.get_by_role("heading", name="Before we begin")).to_be_visible()
            expect(
                page.get_by_role("button", name="Optional email choices", exact=True)
            ).to_have_count(0)
            page.get_by_label("Recipient first name").fill("José")
            page.get_by_label("This invitation was sent to", exact=True).select_option(
                role
            )
            page.get_by_role("button", name="Begin Visit Prep").click()
            expect(
                page.get_by_role(
                    "heading", name="What is the main reason for this visit?"
                )
            ).to_be_visible()
            assert reads == 0 and not decisions
            page.get_by_role("textbox").fill("An unsaved synthetic reason.")
            page.get_by_role(
                "button", name="Optional email choices", exact=True
            ).click()
            expect(
                page.get_by_role("heading", name="Optional email choices")
            ).to_be_visible()
            page.get_by_role("button", name="Back to preparation").click()
            expect(page.get_by_role("textbox")).to_have_value(
                "An unsaved synthetic reason."
            )
            assert reads == 0 and not decisions

            def open_choices():
                page.get_by_role(
                    "button", name="Optional email choices", exact=True
                ).click()

            def load_choices():
                page.get_by_label("Recipient first name").fill("José")
                page.get_by_label(
                    "This invitation was sent to", exact=True
                ).select_option(role)
                page.get_by_role("button", name="Load current email choices").click()

            def attest():
                page.get_by_label("I reviewed these disclosures", exact=False).check()
                if role == "caregiver":
                    page.get_by_label(
                        "I am authorized to make this communication choice", exact=False
                    ).check()

            open_choices()
            load_choices()
            expect(
                page.get_by_text("No email choice recorded at last load", exact=True)
            ).to_be_visible()
            expect(
                page.get_by_role("button", name="Record permission choice")
            ).to_be_disabled()
            expiry = (datetime.now(UTC) + timedelta(days=2)).strftime("%Y-%m-%dT%H:%M")
            page.get_by_label("Permission expiry (UTC)").fill(expiry)
            attest()
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            directory = root / "tmp"
            directory.mkdir(exist_ok=True)
            page.screenshot(
                path=str(directory / f"visit-prep-email-choice-{role}-{width}.png"),
                full_page=True,
            )
            page.get_by_role("button", name="Record permission choice").click()
            expect(
                page.get_by_text("A choice may have been saved", exact=False)
            ).to_be_visible()
            expect(
                page.get_by_role("button", name="Record permission choice")
            ).to_have_count(0)
            assert len(decisions) == 1
            load_choices()
            expect(
                page.get_by_text(
                    "Permission choice recorded at last load — sending not enabled",
                    exact=True,
                )
            ).to_be_visible()
            assert len(decisions) == 1 and reads == 2
            page.get_by_role("button", name="Back to preparation").click()
            for prompt in (
                "What is the main reason for this visit?",
                "What medications are you prescribed right now?",
                "Are you taking them as prescribed?",
                "Tell me your health story — whatever feels relevant.",
            ):
                expect(page.get_by_role("heading", name=prompt)).to_be_visible()
                page.get_by_role("button", name="Skip this topic").click()
            expect(
                page.get_by_role("heading", name="Your topics are saved")
            ).to_be_visible()
            page.get_by_role("button", name="Finish Visit Prep").click()
            expect(
                page.get_by_role("heading", name="Visit Prep complete")
            ).to_be_visible()
            open_choices()
            load_choices()
            expect(
                page.get_by_text("New permission cannot be recorded", exact=False)
            ).to_be_visible()
            expect(
                page.get_by_role("button", name="Record permission choice")
            ).to_have_count(0)
            page.get_by_role(
                "button", name="Do not allow / withdraw email invitations"
            ).click()
            expect(
                page.get_by_text("Your decision was recorded", exact=False)
            ).to_be_visible()
            load_choices()
            expect(
                page.get_by_text("Email permission withdrawn at last load", exact=True)
            ).to_be_visible()
            assert len(decisions) == 2 and reads == 4
            assert decisions[0][0] != decisions[1][0]
            page.screenshot(
                path=str(directory / f"visit-prep-email-withdrawn-{role}-{width}.png"),
                full_page=True,
            )
            page.evaluate("window.dispatchEvent(new Event('blur'))")
            expect(page.get_by_label("Recipient first name")).to_have_value("")
            expect(
                page.get_by_text("Email permission withdrawn at last load", exact=True)
            ).to_have_count(0)
            fixture.practice.negotiated_baa_on_file = False
            fixture.practice.save()
            load_choices()
            expect(
                page.get_by_text("Email choices could not be confirmed", exact=False)
            ).to_be_visible()
            expect(
                page.get_by_text("Email permission withdrawn at last load", exact=True)
            ).to_have_count(0)
            page.evaluate(
                "window.dispatchEvent(new Event('cosmaneura:view-as-hard-block'))"
            )
            expect(
                page.get_by_role("heading", name="Optional email choices")
            ).to_have_count(0)
            assert fixture.access_token not in page.content()
            assert page.url == "https://testserver/visit-prep"
            assert (
                page.evaluate(
                    "JSON.stringify([Object.entries(localStorage), Object.entries(sessionStorage)])"
                )
                == '[[["loglevel","INFO"]],[]]'
            )
            # The shared SPA index already links this public stylesheet. It is
            # aborted above, never fetched. Accept no other external URL and
            # do not describe the inherited font link as a private API call.
            assert all(
                url
                == "https://fonts.googleapis.com/css2?family=Inter:ital,opsz,wght@0,14..32,100..900;1,14..32,100..900&family=Cormorant+Garamond:wght@500;600;700&display=swap"
                for url in forbidden
            )
        finally:
            browser.close()
    rows = _rows(fixture)
    assert len(rows) == 2
    assert [read_email_consent(row).event_kind for row in rows] == [
        "granted",
        "withdrawn",
    ]
    assert all(read_email_consent(row).actor_role == role for row in rows)
    with get_session() as session:
        prep = session.get(VisitPrep, fixture.prep_id)
        assert prep is not None and prep.completed_at is not None
        assert not session.exec(
            select(VisitPrepEmailDelivery).where(
                VisitPrepEmailDelivery.visit_prep_id == fixture.prep_id
            )
        ).all()
