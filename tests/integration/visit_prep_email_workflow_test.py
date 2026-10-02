"""Actual provider handoff and scheduled worker, invented recipient/SMTP only."""

from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from app.configuration import visit_prep, visit_prep_email_source
from app.configuration.visit_prep_email_source import (
    load_patient_email_source_qualified,
)
from app.jobs import visit_prep_email as job
from app.services import visit_prep_access
from app.services.visit_prep_email_contract import VisitPrepEmailInvalid
from app.services.visit_prep_email_evidence import (
    email_policy_fingerprint,
    read_email_consent,
)
from app.services.visit_prep_email_policy import record_email_policy_decision
from app.services.visit_prep_email_source import verify_patient_email_source

from activemodel.session_manager import get_session
from app.models.visit_prep_delivery import VisitPrepEmailDelivery
from sqlmodel import select

from tests.routes.visit_prep_question_sets_test import _provider_client
from tests.services.visit_prep_email_consent_test import _capture, _decision, _rows
from tests.services.visit_prep_email_consent_view_test import _ready


def _fixture(monkeypatch, *, role="patient", quiet=False):
    fixture = _ready(monkeypatch, "email-workflow", role=role)
    _capture(fixture, _decision(fixture, role=role))
    settings = replace(visit_prep_access.VISIT_PREP_SETTINGS, email_send_enabled=True)
    monkeypatch.setattr(visit_prep_access, "VISIT_PREP_SETTINGS", settings)
    monkeypatch.setattr(visit_prep, "VISIT_PREP_SETTINGS", settings)
    monkeypatch.setattr(visit_prep_email_source, "PATIENT_EMAIL_SOURCE_QUALIFIED", True)
    record_email_policy_decision(
        owner_practice_member_id=fixture.owner.id,
        owner_doctor_id=fixture.doctor.id,
        practice_id=fixture.practice.id,
        expected_clerk_subject=fixture.doctor.clerk_id,
        operation_id=str(uuid4()),
        expected_event_sequence=0,
        event_kind="approved",
        policy_fingerprint=email_policy_fingerprint(),
        expires_at=fixture.scheduled_at,
        settings=settings,
    )
    # Select real current local noon/quiet time, never mock the database clock.
    from app.services.visit_preps import reschedule_visit_prep

    hour = datetime.now(UTC).hour
    offset = ((22 if quiet else 12) - hour + 12) % 24 - 12
    zone = "UTC" if offset == 0 else f"Etc/GMT{-offset:+d}"
    changed = reschedule_visit_prep(
        owner_practice_member_id=fixture.owner.id,
        owner_doctor_id=fixture.doctor.id,
        practice_id=fixture.practice.id,
        visit_prep_id=fixture.prep_id,
        expected_schedule_generation=1,
        expected_credential_generation=1,
        scheduled_at=fixture.scheduled_at.astimezone(ZoneInfo(zone)),
        timezone_name=zone,
    )
    fixture = replace(fixture, access_token=changed.access_token)
    return fixture, changed.visit_prep.credential_generation


def _post(client, fixture, generation, *, key=None, **changes):
    return client.post(
        f"/internal/v1/visit-prep/preps/{fixture.prep_id}/email-invitation",
        headers={"Idempotency-Key": key or str(uuid4())},
        json={
            "expected_credential_generation": generation,
            "access_token": fixture.access_token,
        }
        | changes,
    )


def _deliveries(fixture):
    with get_session() as session:
        return session.exec(
            select(VisitPrepEmailDelivery).where(
                VisitPrepEmailDelivery.visit_prep_id == fixture.prep_id
            )
        ).all()


@pytest.mark.parametrize("role", ["patient", "caregiver"])
def test_actual_handoff_scheduled_submission_and_exact_replay(monkeypatch, role):
    fixture, generation = _fixture(monkeypatch, role=role)
    key, calls = str(uuid4()), []
    with _provider_client(fixture.doctor, monkeypatch) as client:
        queued = _post(client, fixture, generation, key=key)
        assert queued.status_code == 200 and queued.json()["state"] == "queued"
        assert queued.json()["dispatch_integration"] == "default_off_worker"
        assert fixture.access_token not in queued.text
        assert queued.headers["cache-control"] == "no-store"
        assert queued.headers["referrer-policy"] == "no-referrer"
        monkeypatch.setattr(
            job.CoreSMTPInvitationTransport,
            "from_configured_mailer",
            lambda: lambda message: calls.append(message) or "accepted",
        )
        job.perform()
        assert len(calls) == 1 and calls[0].access_token == fixture.access_token
        assert _deliveries(fixture)[0].state == "accepted"
        assert _deliveries(fixture)[0].payload_ciphertext is None
        replay = _post(client, fixture, generation, key=key)
        assert replay.json()["disposition"] == "replayed"
        assert replay.json()["state"] == "accepted"
        job.perform()
        assert len(calls) == len(_deliveries(fixture)) == 1


def test_quiet_hours_persist_one_intent_without_transport_then_dispatch(monkeypatch):
    fixture, generation = _fixture(monkeypatch, quiet=True)
    calls = []
    with _provider_client(fixture.doctor, monkeypatch) as client:
        assert _post(client, fixture, generation).status_code == 200
    monkeypatch.setattr(
        job.CoreSMTPInvitationTransport,
        "from_configured_mailer",
        lambda: lambda message: calls.append(message) or "accepted",
    )
    before = _deliveries(fixture)[0]
    job.perform()
    after = _deliveries(fixture)[0]
    assert (
        after.state == "queued"
        and after.payload_ciphertext == before.payload_ciphertext
    )
    assert after.submitted_at is None and not calls
    # TEST ONLY central policy change, not a provider override.
    from app.configuration import visit_prep_cadence

    monkeypatch.setattr(
        visit_prep_cadence,
        "VISIT_PREP_CADENCE_SETTINGS",
        replace(
            visit_prep_cadence.DEFAULT_VISIT_PREP_CADENCE,
            version="synthetic-email-workflow",
            quiet_start_hour=4,
            quiet_end_hour=5,
        ),
    )
    job.perform()
    assert len(calls) == 1 and _deliveries(fixture)[0].state == "accepted"


@pytest.mark.parametrize(
    "failure", ["qualification", "withdrawn", "wrong_token", "extra"]
)
def test_missing_current_permission_or_invalid_input_never_queues(monkeypatch, failure):
    fixture, generation = _fixture(monkeypatch)
    changes = {}
    if failure == "qualification":
        monkeypatch.setattr(
            visit_prep_email_source, "PATIENT_EMAIL_SOURCE_QUALIFIED", False
        )
    elif failure == "withdrawn":
        _capture(fixture, _decision(fixture, kind="withdrawn", sequence=1))
    elif failure == "wrong_token":
        changes["access_token"] = "vp1_" + "A" * 43
    else:
        changes["patient_origin"] = "https://untrusted.example.invalid"
    with _provider_client(fixture.doctor, monkeypatch) as client:
        response = _post(client, fixture, generation, **changes)
    assert response.status_code in {409, 422} and not _deliveries(fixture)
    assert fixture.access_token not in response.text


def test_transport_unavailable_does_not_consume_claim_and_uncertain_never_resends(
    monkeypatch,
):
    fixture, generation = _fixture(monkeypatch)
    with _provider_client(fixture.doctor, monkeypatch) as client:
        assert _post(client, fixture, generation).status_code == 200
    calls = []

    def unavailable():
        raise ValueError("invented private SMTP diagnostic")

    monkeypatch.setattr(
        job.CoreSMTPInvitationTransport, "from_configured_mailer", unavailable
    )
    job.perform()
    assert _deliveries(fixture)[0].state == "queued"

    def uncertain(message):
        calls.append(message)
        raise TimeoutError("invented private response lost")

    monkeypatch.setattr(
        job.CoreSMTPInvitationTransport, "from_configured_mailer", lambda: uncertain
    )
    job.perform()
    job.perform()
    assert len(calls) == 1 and _deliveries(fixture)[0].state == "uncertain"
    assert _deliveries(fixture)[0].payload_ciphertext is None


def test_actual_withdrawal_after_queue_suppresses_before_transport(monkeypatch):
    fixture, generation = _fixture(monkeypatch)
    with _provider_client(fixture.doctor, monkeypatch) as client:
        assert _post(client, fixture, generation).status_code == 200
    _capture(fixture, _decision(fixture, kind="withdrawn", sequence=1))
    calls = []
    monkeypatch.setattr(
        job.CoreSMTPInvitationTransport,
        "from_configured_mailer",
        lambda: lambda message: calls.append(message) or "accepted",
    )
    job.perform()
    row = _deliveries(fixture)[0]
    assert not calls and row.state == "suppressed"
    assert row.payload_ciphertext is None


def test_provider_handoff_bounds_private_request_before_json_validation(monkeypatch):
    fixture, _ = _fixture(monkeypatch)
    with _provider_client(fixture.doctor, monkeypatch) as client:
        response = client.post(
            f"/internal/v1/visit-prep/preps/{fixture.prep_id}/email-invitation",
            headers={
                "Idempotency-Key": str(uuid4()),
                "Content-Type": "application/json",
            },
            content="invented-private-input" * 100,
        )
    assert response.status_code == 413 and not _deliveries(fixture)
    assert "invented-private-input" not in response.text
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "no-referrer"


def test_qualification_is_not_a_source_and_worker_is_default_off(monkeypatch):
    fixture, _ = _fixture(monkeypatch)
    evidence = read_email_consent(_rows(fixture)[0])
    with get_session() as session:
        assert verify_patient_email_source(
            session, evidence, checked_at=datetime.now(UTC)
        )
        with pytest.raises(VisitPrepEmailInvalid):
            verify_patient_email_source(
                session,
                replace(evidence, source_payload_digest="a" * 64),
                checked_at=datetime.now(UTC),
            )
    monkeypatch.setattr(
        visit_prep_email_source, "PATIENT_EMAIL_SOURCE_QUALIFIED", False
    )
    monkeypatch.setattr(
        job.CoreSMTPInvitationTransport,
        "from_configured_mailer",
        lambda: pytest.fail("disabled worker constructed transport"),
    )
    job.perform()
    assert load_patient_email_source_qualified({}) is False
    assert (
        load_patient_email_source_qualified(
            {"VISIT_PREP_PATIENT_EMAIL_SOURCE_QUALIFIED": "typo"}
        )
        is False
    )
    assert job.perform_celery.max_retries == 0 and job.perform_celery.ignore_result
