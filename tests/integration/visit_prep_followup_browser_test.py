"""Built follow-up capture and explicit wording choice; owned synthetic API only."""

from urllib.parse import urlsplit
from uuid import uuid4

import pytest
from playwright.sync_api import expect, sync_playwright

from app import root
from app.services.visit_prep_clarification_contract import (
    PatientClarificationReply,
    PatientQuoteSpan,
    append_patient_clarification,
)
from app.services.visit_prep_clarification_turns import (
    record_patient_clarification_turn,
)

from tests.integration.visit_prep_clarification_http_test import _client
from tests.integration.visit_prep_clarification_turns_test import (
    WORDS,
    _counts,
    _save,
    _start,
)
from tests.services.visit_prep_contribution_projection_test import _projection, _topic


@pytest.mark.parametrize("width,height", [(1440, 1000), (390, 844)])
@pytest.mark.parametrize("role", ["patient", "caregiver"])
def test_built_followup_exact_retry_then_explicit_wording_and_review(
    monkeypatch, owned_rate_limit_redis, width, height, role
):
    assert (root / "web/build/test/client/index.html").is_file()
    fixture, original, proposal = _start(monkeypatch, role=role)
    client = _client(monkeypatch, owned_rate_limit_redis)
    submissions: list[tuple[str, bytes]] = []
    private_paths: list[str] = []
    forbidden: list[str] = []
    seen_urls: list[str] = []
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
                seen_urls.append(request.url)
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
                    private_paths.append(url.path)
                if url.path.endswith("/clarification/turn"):
                    assert request.post_data_buffer is not None
                    submissions.append(
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
                if url.path.endswith("/clarification/turn") and len(submissions) == 1:
                    assert response.status_code == 200
                    browser_route.fulfill(status=503, body="Synthetic lost receipt")
                    return
                assert response.status_code != 429, private_paths
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
                name="Check follow-up: Tell me your health story — whatever feels relevant.",
                exact=True,
            ).click()
            expect(
                page.get_by_role("heading", name="Clarify your answer")
            ).to_be_visible()
            assert not submissions
            page.get_by_label(
                "Is there anything you would like to make clearer about this answer?"
            ).fill(WORDS)
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            (root / "tmp").mkdir(exist_ok=True)
            page.screenshot(
                path=str(root / "tmp" / f"visit-prep-followup-{width}-{role}.png"),
                full_page=True,
            )
            page.get_by_role("button", name="Save follow-up answer", exact=True).click()
            expect(page.get_by_role("button", name="Retry same save")).to_be_visible()
            expect(
                page.get_by_role("button", name="Save follow-up answer")
            ).to_be_disabled()
            assert _topic(_projection(fixture), "story").answer == original.answer
            page.get_by_role("button", name="Retry same save").click()
            expect(
                page.get_by_text("Your follow-up answer is saved.", exact=False)
            ).to_be_visible()
            assert len(submissions) == 2 and submissions[0] == submissions[1]
            assert _counts(fixture) == (1, 1)
            assert sum(p.endswith("/follow-up") for p in private_paths) == 1

            # Stand in for Sovereign with an invented, actual-source-bound reply.
            # The browser never submits model context or substitutes patient words.
            clarified = append_patient_clarification(
                original,
                PatientClarificationReply(
                    kind="clarify", prompt_code="detail", statements=()
                ),
                patient_words=WORDS,
            )
            turn = record_patient_clarification_turn(
                access_token=fixture.access_token,
                first_name="José",
                role=role,
                proposal_id=proposal.proposal_id,
                operation_id=submissions[0][0],
                patient_words=WORDS,
            )
            _save(
                fixture,
                clarified,
                turn=turn,
                operation=str(uuid4()),
                reply=PatientClarificationReply(
                    kind="organize",
                    prompt_code=None,
                    statements=(
                        PatientQuoteSpan(
                            source="answer",
                            start=0,
                            end=len(original.answer),
                            quote=original.answer,
                        ),
                        PatientQuoteSpan(
                            source="clarification_1",
                            start=0,
                            end=len(WORDS),
                            quote=WORDS,
                        ),
                    ),
                ),
            )
            page.get_by_role("button", name="Back to saved answers").click()
            page.get_by_role(
                "button",
                name="Review wording proposal: Tell me your health story — whatever feels relevant.",
                exact=True,
            ).click()
            expect(
                page.get_by_role("heading", name="Review proposed wording")
            ).to_be_visible()
            assert _topic(_projection(fixture), "story").answer == original.answer
            page.get_by_role("button", name="Accept proposed wording").click()
            expect(
                page.get_by_role("heading", name="Your saved answers")
            ).to_be_visible()
            page.get_by_role("button", name="Confirm my answers").click()
            expect(
                page.get_by_text("You confirmed these saved answers.", exact=False)
            ).to_be_visible()
            accepted = _topic(_projection(fixture), "story")
            assert accepted.answer == original.answer + "\n" + WORDS
            assert accepted.original_answer == original.answer
            assert _projection(fixture).current_answers_reviewed
            assert fixture.access_token not in page.content()
            stores = page.evaluate(
                "JSON.stringify([Object.entries(localStorage), Object.entries(sessionStorage)])"
            )
            assert all(
                v not in stores for v in (fixture.access_token, WORDS, original.answer)
            )
            assert all(fixture.access_token not in url for url in seen_urls)
            assert page.evaluate("window.Clerk === undefined")
            page.evaluate(
                "window.dispatchEvent(new Event('cosmaneura:view-as-hard-block'))"
            )
            expect(page.get_by_text(accepted.answer, exact=True)).to_have_count(0)
            assert all(
                urlsplit(url).netloc == "fonts.googleapis.com" for url in forbidden
            )
        finally:
            browser.close()
