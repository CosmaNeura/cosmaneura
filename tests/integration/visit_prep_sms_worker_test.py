"""Actual SMS queue and one-attempt worker; synthetic qualification, no provider."""

import pickle
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.configuration import visit_prep_sms
from app.services import visit_prep_sms_outbox as outbox
from app.services import visit_prep_sms_worker as worker
from app.services.visit_prep_sms_consent import (
    PatientSMSConsentInput,
    get_patient_sms_consent_view,
    record_patient_sms_consent,
)
from app.services.visit_prep_sms_contract import (
    VisitPrepSMSUnavailable,
)
from app.services.visit_prep_sms_suppression import (
    SMSSuppressionUnavailable,
)
from app.services.visit_preps import rotate_visit_prep_credential
from app.utils.encryption import decrypt_value

from activemodel.session_manager import get_engine
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlmodel import Session

from tests.integration.visit_prep_sms_outbox_test import _ready, _row
from tests.integration.visit_prep_sms_suppression_test import _event, _record


def _worker(options, **extra):
    return {
        key: options[key]
        for key in ("settings", "approval_verifier", "consent_verifier")
    } | extra


def test_queue_exact_retry_private_evidence_and_real_single_submission(monkeypatch):
    fixture, options, _ = _ready(monkeypatch)
    receipt = outbox.enqueue_sms(**options)
    assert receipt.disposition == "queued"
    assert outbox.enqueue_sms(**options).delivery_id == receipt.delivery_id
    with pytest.raises(VisitPrepSMSUnavailable):
        outbox.enqueue_sms(**(options | {"operation_id": str(uuid4())}))
    row = _row(receipt)
    retained = decrypt_value(row.evidence_ciphertext)
    assert all(
        value not in retained
        for value in (
            fixture.access_token,
            "+12025550123",
            "José",
            "patient@example.com",
        )
    )
    seen = []
    assert (
        "payload_ciphertext" not in row.model_dump()
        and "evidence_ciphertext" not in row.model_dump()
    )
    claim = worker.claim_sms_invitation(**_worker(options))
    assert isinstance(claim, worker.SMSSubmissionClaim)
    with pytest.raises(TypeError):
        pickle.dumps(claim)

    def transport(message):
        seen.append(message)
        assert message.recipient == "+12025550123" and "Reply STOP" in message.body
        assert fixture.access_token in message.body and "José" not in message.body
        return "accepted"

    assert (
        worker.dispatch_sms_claim(claim, **_worker(options, transport=transport))
        == "accepted"
    )
    assert (
        worker.dispatch_sms_claim(claim, **_worker(options, transport=transport))
        == "discarded"
    )
    assert worker.run_one_sms(**_worker(options, transport=transport)) == "idle"
    final = _row(receipt)
    assert (
        len(seen) == 1
        and final.state == "accepted"
        and final.payload_ciphertext is None
    )
    assert final.evidence_ciphertext == row.evidence_ciphertext


@pytest.mark.parametrize("failure", ["exception", "unknown"])
def test_unknown_provider_result_is_never_requeued(monkeypatch, failure):
    _, options, _ = _ready(monkeypatch)
    receipt = outbox.enqueue_sms(**options)
    seen = []

    def transport(message):
        seen.append(message)
        if failure == "exception":
            raise ValueError("Synthetic private provider error must not escape")
        return "delivered"

    assert worker.run_one_sms(**_worker(options, transport=transport)) == "uncertain"
    assert worker.run_one_sms(**_worker(options, transport=transport)) == "idle"
    assert len(seen) == 1 and _row(receipt).payload_ciphertext is None


@pytest.mark.parametrize(
    "change", ["stop", "withdraw", "rotate", "approval", "disabled"]
)
def test_changes_between_claim_and_dispatch_block_the_effect(monkeypatch, change):
    fixture, options, routing = _ready(monkeypatch)
    receipt = outbox.enqueue_sms(**options)
    claim = worker.claim_sms_invitation(**_worker(options))
    assert isinstance(claim, worker.SMSSubmissionClaim)
    if change == "stop":
        _record(_event(routing))
    elif change == "withdraw":
        view = get_patient_sms_consent_view(
            access_token=fixture.access_token, first_name="José", role="patient"
        )
        record_patient_sms_consent(
            access_token=fixture.access_token,
            first_name="José",
            decision=PatientSMSConsentInput(
                str(uuid4()),
                1,
                "withdrawn",
                "patient",
                view.withdrawal_disclosure_fingerprint,
                None,
                False,
                False,
            ),
        )
    elif change == "rotate":
        rotate_visit_prep_credential(
            owner_practice_member_id=fixture.owner.id,
            owner_doctor_id=fixture.doctor.id,
            practice_id=fixture.practice.id,
            visit_prep_id=fixture.prep_id,
            expected_credential_generation=1,
        )
    elif change == "approval":
        options["approval_verifier"] = lambda *a, **kw: False
    else:
        options["settings"] = replace(options["settings"], sms_send_enabled=False)

    def forbidden(message):
        raise AssertionError("transport must not run")

    assert (
        worker.dispatch_sms_claim(claim, **_worker(options, transport=forbidden))
        == "suppressed"
    )
    assert _row(receipt).payload_ciphertext is None


def test_shared_sender_spacing_and_sql_intent_guards(monkeypatch):
    _, options, routing = _ready(monkeypatch)
    receipt = outbox.enqueue_sms(**options)
    assert (
        worker.run_one_sms(**_worker(options, transport=lambda m: "accepted"))
        == "accepted"
    )
    _, second, _ = _ready(monkeypatch, routing=routing)
    assert outbox.enqueue_sms(**second).disposition == "wait"
    for statement in (
        "UPDATE visit_prep_sms_delivery SET step='unfinished_12h_sms' WHERE operation_id=:op",
        "UPDATE visit_prep_sms_delivery SET state='queued' WHERE operation_id=:op",
        "DELETE FROM visit_prep_sms_delivery WHERE operation_id=:op",
        "TRUNCATE visit_prep_sms_delivery",
    ):
        with Session(get_engine()) as session, pytest.raises(DBAPIError):
            session.execute(text(statement), {"op": options["operation_id"]})
            session.commit()
    assert _row(receipt).state == "accepted"


def test_transport_holds_the_actual_sender_stop_fence(monkeypatch):
    _, options, routing = _ready(monkeypatch)
    outbox.enqueue_sms(**options)
    event = _event(routing)

    def transport(message):
        with ThreadPoolExecutor(max_workers=1) as pool:
            with pytest.raises(SMSSuppressionUnavailable):
                pool.submit(_record, event).result(timeout=5)
        return "accepted"

    assert worker.run_one_sms(**_worker(options, transport=transport)) == "accepted"
    assert _record(event).disposition == "recorded"


def test_late_transport_response_is_uncertain_and_dark_maintenance_clears_stop(
    monkeypatch,
):
    _, options, _ = _ready(monkeypatch)
    receipt = outbox.enqueue_sms(**options)
    clock = iter([0.0, 31.0])
    monkeypatch.setattr(worker, "monotonic", lambda: next(clock))
    assert (
        worker.run_one_sms(**_worker(options, transport=lambda m: "accepted"))
        == "uncertain"
    )
    assert _row(receipt).payload_ciphertext is None
    _, second, _ = _ready(monkeypatch)
    pending = outbox.enqueue_sms(**second)
    _record(_event(visit_prep_sms.ROUTING))
    assert worker.run_one_sms(**_worker(second, transport=None)) == "disabled"
    assert worker.maintain_sms_secrets()["cleared"] == 1
    assert (
        _row(pending).state == "suppressed" and _row(pending).payload_ciphertext is None
    )


def _age_owned_row(row, changes):
    # Explicit fixture aging ONLY in the runner's fresh synthetic database.
    # Normal application and raw-transition tests keep the guard enabled.
    with Session(get_engine()) as session:
        session.execute(
            text(
                "ALTER TABLE visit_prep_sms_delivery DISABLE TRIGGER visit_prep_sms_delivery_guard"
            )
        )
        session.execute(
            text(f"UPDATE visit_prep_sms_delivery SET {changes} WHERE id=:id"),
            {"id": row.id.uuid},
        )
        session.execute(
            text(
                "ALTER TABLE visit_prep_sms_delivery ENABLE TRIGGER visit_prep_sms_delivery_guard"
            )
        )
        session.commit()


def test_actual_unfinished_reminder_uses_retained_booking_history(monkeypatch):
    fixture, options, _ = _ready(
        monkeypatch, scheduled_at=datetime.now(UTC) + timedelta(hours=10)
    )
    with Session(get_engine()) as session:
        session.execute(
            text(
                "ALTER TABLE visit_prep_credential_generation DISABLE TRIGGER visit_prep_credential_guard_mutation"
            )
        )
        session.execute(
            text(
                "UPDATE visit_prep_credential_generation SET issued_at=clock_timestamp()-interval '20 hours' WHERE id=:id"
            ),
            {"id": options["credential_id"].uuid},
        )
        session.execute(
            text(
                "ALTER TABLE visit_prep_credential_generation ENABLE TRIGGER visit_prep_credential_guard_mutation"
            )
        )
        session.commit()
    first = outbox.enqueue_sms(**options)
    assert (
        worker.run_one_sms(**_worker(options, transport=lambda m: "accepted"))
        == "accepted"
    )
    _age_owned_row(
        _row(first),
        "requested_at=clock_timestamp()-interval '6 hours', submitted_at=clock_timestamp()-interval '5 hours', finalized_at=clock_timestamp()-interval '5 hours'",
    )
    reminder = outbox.enqueue_sms(
        **(options | {"step": "unfinished_12h_sms", "operation_id": str(uuid4())})
    )
    assert reminder.disposition == "queued"
    seen = []

    def transport(message):
        seen.append(message.step)
        return "accepted"

    assert worker.run_one_sms(**_worker(options, transport=transport)) == "accepted"
    assert seen == ["unfinished_12h_sms"]
    assert (
        outbox.enqueue_sms(
            **(options | {"step": "unopened_48h_sms", "operation_id": str(uuid4())})
        ).reason
        == "short_notice"
    )


def test_two_actual_claimers_share_one_irreversible_lane(monkeypatch):
    _, options, _ = _ready(monkeypatch)
    receipt = outbox.enqueue_sms(**options)
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(
            pool.map(
                lambda _: worker.claim_sms_invitation(**_worker(options)), range(2)
            )
        )
    actual = [claim for claim in claims if isinstance(claim, worker.SMSSubmissionClaim)]
    assert len(actual) == 1
    assert (
        worker.dispatch_sms_claim(
            actual[0], **_worker(options, transport=lambda m: "accepted")
        )
        == "accepted"
    )
    assert _row(receipt).state == "accepted"


def test_cleanup_due_row_is_not_hidden_behind_healthy_pending_work(monkeypatch):
    _, healthy, _ = _ready(monkeypatch)
    first = outbox.enqueue_sms(**healthy)
    _, stopped, routing = _ready(monkeypatch)
    second = outbox.enqueue_sms(**stopped)
    _record(_event(routing))
    assert worker.maintain_sms_secrets(limit=1)["cleared"] == 1
    assert _row(first).state == "queued" and _row(second).state == "suppressed"
    # Resolve the remaining owned fixture via normal guarded terminal transition.
    with Session(get_engine()) as session:
        row = worker._lock_row(session, outbox.TypeID.from_string(first.delivery_id))
        worker._finish(session, row, "suppressed", "scope_unavailable")


def test_expired_claim_cleanup_is_uncertain_even_with_missing_verifiers(monkeypatch):
    _, options, _ = _ready(monkeypatch)
    receipt = outbox.enqueue_sms(**options)
    claim = worker.claim_sms_invitation(**_worker(options))
    assert isinstance(claim, worker.SMSSubmissionClaim)
    _age_owned_row(
        _row(receipt),
        "requested_at=clock_timestamp()-interval '4 minutes', submitted_at=clock_timestamp()-interval '3 minutes', lease_until=clock_timestamp()-interval '1 minute'",
    )
    assert worker.claim_sms_invitation(settings=options["settings"]) == "disabled"
    assert worker.maintain_sms_secrets()["cleared"] == 1
    assert (
        _row(receipt).state == "uncertain" and _row(receipt).payload_ciphertext is None
    )
