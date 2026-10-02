"""Built actual creator/email UI to real provider API; synthetic delivery only."""

from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit
from uuid import uuid4

import pytest
from playwright.sync_api import expect, sync_playwright

from app import root
from app.configuration import visit_prep, visit_prep_email_source
from app.configuration.visit_prep import VisitPrepSettings
from app.jobs import visit_prep_email as job
from app.services import visit_prep_access
from app.services.visit_prep_email_consent import (
    PatientEmailConsentInput,
    email_consent_disclosure_fingerprint,
    record_patient_email_consent,
)
from app.services.visit_prep_email_evidence import email_policy_fingerprint
from app.services.visit_prep_email_policy import record_email_policy_decision
from app.services.visit_prep_interviews import confirm_interview

from activemodel.session_manager import get_session
from app.models.visit_prep_delivery import VisitPrepEmailDelivery
from sqlmodel import select

from tests.routes.visit_prep_question_sets_test import _provider_client
from tests.services.visit_preps_test import _scope


@pytest.mark.parametrize("width,height", [(1440, 1000), (390, 844)])
def test_provider_creates_and_queues_existing_invitation_then_reviews_acceptance(
    monkeypatch, width, height
):
    scope = _scope(f"email-browser-{width}", now=datetime.now(UTC))
    settings = VisitPrepSettings(
        enabled=True,
        public_api_enabled=True,
        email_send_enabled=True,
        email_consent_capture_enabled=True,
        enabled_doctor_ids=frozenset({str(scope.doctor.id)}),
    )
    monkeypatch.setattr(visit_prep_access, "VISIT_PREP_SETTINGS", settings)
    monkeypatch.setattr(visit_prep, "VISIT_PREP_SETTINGS", settings)
    monkeypatch.setattr(visit_prep_email_source, "PATIENT_EMAIL_SOURCE_QUALIFIED", True)
    record_email_policy_decision(
        owner_practice_member_id=scope.owner.id,
        owner_doctor_id=scope.doctor.id,
        practice_id=scope.practice.id,
        expected_clerk_subject=scope.doctor.clerk_id,
        operation_id=str(uuid4()),
        expected_event_sequence=0,
        event_kind="approved",
        policy_fingerprint=email_policy_fingerprint(),
        expires_at=datetime.now(UTC) + timedelta(days=25),
        settings=settings,
    )
    calls, requests, created = [], [], {}
    # Keep the real database clock while choosing a daytime appointment timezone.
    offset = (12 - datetime.now(UTC).hour + 12) % 24 - 12
    timezone_name = "UTC" if offset == 0 else f"Etc/GMT{-offset:+d}"
    monkeypatch.setattr(
        job.CoreSMTPInvitationTransport,
        "from_configured_mailer",
        lambda: lambda message: calls.append(message) or "accepted",
    )
    styles = list((root / "web/build/test/client/assets").glob("root-*.css"))
    harness = root / "web/build/test/visit-prep-email-harness.js"
    assert len(styles) == 1 and harness.is_file()
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
                viewport={"width": width, "height": height},
                service_workers="block",
                timezone_id=timezone_name,
            )

            def local(route):
                request, url = route.request, urlsplit(route.request.url)
                assert url.scheme == "https" and url.netloc == "testserver"
                if url.path == "/synthetic-email":
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
                    assert url.path.startswith("/internal/v1/visit-prep/")
                    requests.append((request.method, url.path))
                    response = client.request(
                        request.method,
                        url.path,
                        content=request.post_data_buffer,
                        headers={
                            k: v
                            for k, v in request.headers.items()
                            if k in {"content-type", "idempotency-key"}
                        },
                    )
                    assert response.headers["cache-control"] == "no-store"
                    if url.path.endswith("/email-invitation"):
                        assert response.status_code == 200
                    if (
                        request.method == "POST"
                        and url.path == "/internal/v1/visit-prep/preps"
                    ):
                        assert response.status_code == 201
                        created.update(response.json())
                        token = urlsplit(created["access_url"]).fragment.split("=", 1)[
                            1
                        ]
                        # Explicit synthetic patient capture through actual commands.
                        # This is not prior-booking consent or patient UI qualification.
                        confirm_interview(
                            access_token=token, first_name="José", role="patient"
                        )
                        record_patient_email_consent(
                            access_token=token,
                            decision=PatientEmailConsentInput(
                                operation_id=str(uuid4()),
                                expected_event_sequence=0,
                                event_kind="granted",
                                role="patient",
                                disclosure_fingerprint=email_consent_disclosure_fingerprint(
                                    role="patient", event_kind="granted"
                                ),
                                expires_at=datetime.fromisoformat(
                                    created["visit_prep"]["scheduled_at"]
                                ),
                                recipient_control_attested=True,
                                caregiver_authority_attested=False,
                            ),
                        )
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
            page.goto("https://testserver/synthetic-email")
            page.get_by_label("Recipient first name").fill("José")
            page.get_by_label("Email", exact=True).fill("patient@example.com")
            # Form requires the real browser-local timestamp.
            when = page.evaluate(
                "new Date(Date.now()+14*86400000).toLocaleString('sv-SE').slice(0,16).replace(' ','T')"
            )
            page.get_by_label("Appointment", exact=True).fill(when)
            page.get_by_role("button", name="Create Visit Prep", exact=True).click()
            expect(page.get_by_role("heading", name="Visit Prep ready")).to_be_visible()
            expect(page.get_by_role("alert")).to_have_count(0)
            expect(
                page.get_by_role("button", name="Queue email invitation")
            ).to_be_visible()
            assert not calls and not any(
                path.endswith("email-invitation") for _, path in requests
            )
            page.get_by_role("button", name="Queue email invitation").click()
            expect(page.get_by_role("status")).to_contain_text("Invitation queued")
            expect(
                page.get_by_text("Core holds the invitation handoff", exact=False)
            ).to_be_visible()
            identifier = created["visit_prep"]["id"]
            with get_session() as session:
                row = session.exec(
                    select(VisitPrepEmailDelivery).where(
                        VisitPrepEmailDelivery.visit_prep_id == identifier
                    )
                ).one()
                assert row.state == "queued"
            job.perform()
            assert len(calls) == 1
            page.get_by_role("button", name="Load / reload invitation status").click()
            expect(
                page.get_by_text("Email provider accepted the invitation", exact=True)
            ).to_be_visible()
            assert created["access_url"] not in page.content()
            assert "patient@example.com" not in page.content()
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
                path=str(root / f"tmp/visit-prep-email-workflow-{width}.png"),
                full_page=True,
            )
            job.perform()
            assert len(calls) == 1
        finally:
            browser.close()
