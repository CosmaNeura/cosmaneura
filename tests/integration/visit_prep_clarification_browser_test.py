"""Built patient proposal review over real owned storage/API; no model call."""

from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient
from playwright.sync_api import expect, sync_playwright

import app.routes.visit_prep_interviews as route
from app import root
from app.server import api_app
from app.services.visit_prep_clarification_contract import validate_clarification_reply

from activemodel.session_manager import get_session

from tests.integration.visit_prep_clarification_decisions_test import _counts
from tests.services.visit_prep_clarification_evidence_test import _proposal
from tests.services.visit_prep_contribution_projection_test import _projection, _topic


@pytest.mark.parametrize("width,height", [(1440, 1000), (390, 844)])
@pytest.mark.parametrize("role", ["patient", "caregiver"])
def test_built_proposal_explicit_choice_lost_ack_and_original_retention(
    monkeypatch, owned_rate_limit_redis, width, height, role
):
    assert (root / "web/build/test/client/index.html").is_file()
    fixture, evidence, row = _proposal(monkeypatch, role=role)
    row.save()
    monkeypatch.setattr(
        route.rate_limits, "get_rate_limit_redis", lambda: owned_rate_limit_redis
    )
    client = TestClient(
        api_app,
        base_url="https://testserver",
        client=(f"198.19.0.{10 + (width == 390) + 2 * (role == 'caregiver')}", 50000),
    )
    decisions: list[tuple[str, bytes]] = []
    forbidden: list[str] = []
    requests: list[str] = []
    lost_ack = False
    accepted_words = (
        "Synthetic edited narrative for the visit."
        if role == "caregiver"
        else validate_clarification_reply(evidence.context, evidence.reply)
    )
    assert accepted_words is not None
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
                nonlocal lost_ack
                request = browser_route.request
                url = urlsplit(request.url)
                requests.append(request.url)
                if url.scheme != "https" or url.netloc != "testserver":
                    forbidden.append(request.url)
                    browser_route.abort()
                    return
                assert not url.query and not url.fragment
                assert url.path == "/visit-prep" or url.path.startswith(
                    ("/assets/", "/cosmas", "/external/v1/visit-prep")
                )
                headers = request.all_headers()
                if url.path.startswith("/external/v1/visit-prep"):
                    assert headers["authorization"] == f"Bearer {fixture.access_token}"
                    assert not any(
                        k in headers for k in ("referer", "sentry-trace", "baggage")
                    )
                if url.path.endswith("/clarification/decision"):
                    assert request.post_data_buffer is not None
                    decisions.append(
                        (headers["idempotency-key"], request.post_data_buffer)
                    )
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
                if url.path.endswith("/clarification/decision") and not lost_ack:
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
            expect(
                page.get_by_role("heading", name="Visit Prep complete")
            ).to_be_visible()
            page.get_by_role("button", name="Review or change saved answers").click()
            page.get_by_label("Recipient first name").fill("José")
            page.get_by_label("This invitation was sent to", exact=True).select_option(
                role
            )
            page.get_by_role("button", name="Show saved answers").click()
            expect(
                page.get_by_role("heading", name="Your saved answers")
            ).to_be_visible()
            page.get_by_role(
                "button",
                name="Review wording proposal: Tell me your health story — whatever feels relevant.",
                exact=True,
            ).click()
            expect(
                page.get_by_role("heading", name="Review proposed wording")
            ).to_be_visible()
            expect(
                page.get_by_text(evidence.context.turns[0].patient_words, exact=True)
            ).to_be_visible()
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            (root / "tmp").mkdir(exist_ok=True)
            page.screenshot(
                path=str(root / "tmp" / f"visit-prep-clarification-{width}-{role}.png"),
                full_page=True,
            )
            assert not decisions
            if role == "caregiver":
                page.get_by_role("button", name="Edit proposed wording").click()
                page.get_by_label("Your wording to share").fill(accepted_words)
                page.get_by_role("button", name="Save my edited wording").click()
            else:
                page.get_by_role("button", name="Accept proposed wording").click()
            expect(page.get_by_role("button", name="Retry same save")).to_be_visible()
            expect(
                page.get_by_role("button", name="Keep my current answer")
            ).to_be_disabled()
            page.get_by_role("button", name="Retry same save").click()
            expect(
                page.get_by_role("heading", name="Your saved answers")
            ).to_be_visible()
            expect(page.get_by_text(accepted_words, exact=True)).to_be_visible()
            page.get_by_text("Earlier wording retained", exact=True).click()
            expect(
                page.get_by_text(evidence.context.answer, exact=True)
            ).to_be_visible()
            assert len(decisions) == 2 and decisions[0] == decisions[1]
            assert fixture.access_token not in page.content()
            stores = page.evaluate(
                "JSON.stringify([Object.entries(localStorage), Object.entries(sessionStorage)])"
            )
            assert all(
                v not in stores
                for v in (fixture.access_token, accepted_words, evidence.context.answer)
            )
            assert page.evaluate("window.Clerk === undefined")
            page.evaluate(
                "window.dispatchEvent(new Event('cosmaneura:view-as-hard-block'))"
            )
            expect(page.get_by_text(accepted_words, exact=True)).to_have_count(0)
            expect(page.get_by_text(evidence.context.answer, exact=True)).to_have_count(
                0
            )
            assert all(fixture.access_token not in url for url in requests)
            assert all(
                urlsplit(url).netloc == "fonts.googleapis.com" for url in forbidden
            )
        finally:
            browser.close()
    with get_session() as session:
        assert _counts(session, fixture) == (1, 1, 1)
    topic = _topic(_projection(fixture), "story")
    assert (
        topic.answer == accepted_words
        and topic.original_answer == evidence.context.answer
    )
