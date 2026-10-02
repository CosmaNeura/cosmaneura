"""Actual SMS maintenance/metadata with invented owners and isolated PostgreSQL."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.celery import celery_app
from app.configuration import visit_prep
from app.jobs import visit_prep_sms_maintenance as job
from app.services import visit_prep_interviews as interviews
from app.services import visit_prep_sms_operations as operations
from app.services import visit_prep_sms_outbox as outbox
from app.services import visit_prep_sms_policy as policy
from app.services import visit_prep_sms_worker as worker

from activemodel.session_manager import get_engine
from app.models.visit_prep_sms_delivery import VisitPrepSMSDelivery
from sqlalchemy import event, text
from sqlmodel import Session, select

from tests.integration.visit_prep_sms_outbox_test import (
    _finish_queue_only_fixtures,  # noqa: F401 -- guarded cleanup of only owned fixtures
    _ready,
    _row,
)
from tests.integration.visit_prep_sms_policy_test import _with_durable_policy
from tests.integration.visit_prep_sms_suppression_test import _event, _record
from tests.integration.visit_prep_sms_worker_test import _age_owned_row, _worker
from tests.services.visit_prep_interviews_test import _respond
from tests.services.visit_prep_synthesis_publication_test import _publish


def _sample():
    value = operations.runtime_health()
    assert set(value) == {*operations.COUNT_FIELDS, "status", "queue_timing"}
    assert value["status"] in {"ok", "degraded"}
    assert value["queue_timing"] == "unqualified"
    assert all(
        type(value[name]) is int and value[name] >= 0
        for name in operations.COUNT_FIELDS
    )
    return value


def _capture(monkeypatch):
    logs = []
    monkeypatch.setattr(
        job,
        "log",
        SimpleNamespace(info=lambda *args, **kwargs: logs.append((args, kwargs))),
    )
    return logs


def _retained(fixture):
    with Session(get_engine()) as session:
        result = {}
        for table in (
            "visit_prep_sms_consent_event",
            "visit_prep_sms_approval_event",
            "visit_prep_question_response",
            "visit_prep_synthesis_artifact",
        ):
            # All comparisons are this fixture's records; never assume a globally empty DB.
            result[table] = (
                session.execute(
                    text(
                        f"SELECT to_jsonb(t)::text FROM {table} t WHERE owner_practice_member_id=:owner ORDER BY id"
                    ),
                    {"owner": fixture.owner.id.uuid},
                )
                .scalars()
                .all()
            )
        return result


def test_actual_queue_and_acceptance_metadata_is_one_read_without_envelopes(
    monkeypatch,
):
    _, options, _ = _ready(monkeypatch)
    before = _sample()
    receipt = outbox.enqueue_sms(**options)
    statements = []
    engine = get_engine()

    def observe(_connection, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", observe)
    try:
        queued = _sample()
    finally:
        event.remove(engine, "before_cursor_execute", observe)
    assert queued["queued"] == before["queued"] + 1
    assert len(statements) == 1 and "MATERIALIZED" in statements[0]
    assert all(
        private not in statements[0]
        for private in (
            "ciphertext",
            "contact",
            "evidence_hmac",
            "payload_hmac",
            "request_hmac",
        )
    )
    assert (
        worker.run_one_sms(**_worker(options, transport=lambda message: "accepted"))
        == "accepted"
    )
    accepted = _sample()
    assert accepted["queued"] == before["queued"]
    assert accepted["accepted"] == before["accepted"] + 1
    assert "delivered" not in accepted
    assert _row(receipt).payload_ciphertext is None


def test_registered_cleanup_clears_stop_with_all_release_controls_off_and_retains_records(
    monkeypatch,
):
    fixture, options, routing = _ready(monkeypatch)
    receipt = outbox.enqueue_sms(**options)
    stored, retained = _row(receipt), _retained(fixture)
    _record(_event(routing))
    before = _sample()
    assert before["obsolete_queued"] >= 1
    monkeypatch.setattr(
        visit_prep,
        "VISIT_PREP_SETTINGS",
        replace(
            options["settings"],
            enabled=False,
            public_api_enabled=False,
            sms_send_enabled=False,
        ),
    )
    logs = _capture(monkeypatch)
    assert job.perform() is None
    final = _row(receipt)
    assert final.state == "suppressed" and final.payload_ciphertext is None
    assert final.evidence_ciphertext == stored.evidence_ciphertext
    assert _retained(fixture) == retained
    assert logs[0][1]["cleared"] == 1
    assert logs[0][1]["cleanup_status"] == "available"
    assert logs[0][1]["queued"] == before["queued"] - 1
    assert fixture.access_token not in str(logs)
    assert routing.sender_number not in str(logs) and "+12025550123" not in str(logs)
    assert (
        job.perform_celery.autoretry_for == () and job.perform_celery.max_retries == 0
    )
    assert (
        job.perform_celery.ignore_result
        and not job.perform_celery.store_errors_even_if_ignored
    )
    assert job.perform_celery.time_limit == 30
    assert job.perform_celery.name == "app.jobs.visit_prep_sms_maintenance.perform"
    assert celery_app.conf.beat_schedule["visit_prep_sms_maintenance"] == {
        "task": job.perform_celery.name,
        "schedule": 60.0,
    }
    assert "visit_prep_sms" not in celery_app.conf.beat_schedule


def test_cleanup_of_completed_preparation_keeps_five_answers_and_published_package(
    monkeypatch,
):
    fixture, options, _ = _ready(monkeypatch)
    receipt = outbox.enqueue_sms(**options)
    for _ in range(5):
        current = interviews.get_interview(
            access_token=fixture.access_token
        ).current_question
        if current is None:
            break
        _respond(
            fixture,
            current.key,
            answer="My invented pre-visit story."
            if current.kind == "story"
            else "some"
            if current.key == "adherence"
            else None,
        )
    interviews.complete_interview(access_token=fixture.access_token)
    _publish(fixture)
    retained = _retained(fixture)
    assert len(retained["visit_prep_question_response"]) == 5
    assert len(retained["visit_prep_synthesis_artifact"]) == 1
    _capture(monkeypatch)
    assert job.perform() is None
    assert (
        _row(receipt).state == "suppressed" and _row(receipt).payload_ciphertext is None
    )
    assert _retained(fixture) == retained


def test_expired_payload_is_reported_then_cleared_without_touching_healthy_work(
    monkeypatch,
):
    _, healthy, _ = _ready(monkeypatch)
    first = outbox.enqueue_sms(**healthy)
    _, expired, _ = _ready(monkeypatch)
    second = outbox.enqueue_sms(**expired)
    _age_owned_row(
        _row(second),
        "requested_at=clock_timestamp()-interval '2 hours', expires_at=clock_timestamp()-interval '1 hour'",
    )
    before = _sample()
    assert before["expired_queued"] >= 1 and before["status"] == "degraded"
    assert worker.maintain_sms_secrets(limit=1) == {"status": "available", "cleared": 1}
    assert _row(first).state == "queued" and _row(first).payload_ciphertext is not None
    assert (
        _row(second).state == "suppressed" and _row(second).payload_ciphertext is None
    )
    assert _sample()["expired_queued"] == before["expired_queued"] - 1


def test_durable_approval_withdrawal_and_lost_baa_clear_secret_without_source_qualification(
    monkeypatch,
):
    fixture, options, intent = _with_durable_policy(monkeypatch)
    receipt = outbox.enqueue_sms(**options)
    policy.record_sms_policy_decision(
        **(
            intent
            | {
                "operation_id": str(uuid4()),
                "expected_event_sequence": 1,
                "event_kind": "withdrawn",
                "expires_at": None,
            }
        )
    )
    retained = _retained(fixture)
    before = _sample()
    with Session(get_engine()) as session:
        session.execute(
            text("UPDATE practice SET negotiated_baa_on_file=false WHERE id=:id"),
            {"id": fixture.practice.id.uuid},
        )
        session.commit()
    assert worker.claim_sms_invitation(settings=options["settings"]) == "disabled"
    assert worker.maintain_sms_secrets() == {"status": "available", "cleared": 1}
    assert (
        _row(receipt).state == "suppressed" and _row(receipt).payload_ciphertext is None
    )
    assert _retained(fixture) == retained
    assert _sample()["obsolete_queued"] == before["obsolete_queued"] - 1


def test_overdue_claim_is_observed_then_becomes_uncertain_never_resendable(monkeypatch):
    _, options, _ = _ready(monkeypatch)
    receipt = outbox.enqueue_sms(**options)
    claim = worker.claim_sms_invitation(**_worker(options))
    assert isinstance(claim, worker.SMSSubmissionClaim)
    _age_owned_row(
        _row(receipt),
        "requested_at=clock_timestamp()-interval '4 minutes', submitted_at=clock_timestamp()-interval '3 minutes', lease_until=clock_timestamp()-interval '1 minute'",
    )
    before = _sample()
    assert before["overdue_submitting"] >= 1
    _capture(monkeypatch)
    assert job.perform() is None
    after = _sample()
    assert after["overdue_submitting"] == before["overdue_submitting"] - 1
    assert (
        after["uncertain"] == before["uncertain"] + 1 and after["status"] == "degraded"
    )
    assert _row(receipt).payload_ciphertext is None
    assert (
        worker.dispatch_sms_claim(
            claim,
            **_worker(options, transport=lambda message: pytest.fail("no resend")),
        )
        == "discarded"
    )
    assert (
        worker.run_one_sms(
            **_worker(options, transport=lambda message: pytest.fail("no resend"))
        )
        == "idle"
    )


def test_health_does_not_observe_or_commit_an_ambient_pending_transition(monkeypatch):
    _, options, _ = _ready(monkeypatch)
    receipt = outbox.enqueue_sms(**options)
    before = _sample()
    with Session(get_engine()) as ambient:
        row = ambient.exec(
            select(VisitPrepSMSDelivery).where(
                VisitPrepSMSDelivery.id
                == outbox.TypeID.from_string(receipt.delivery_id)
            )
        ).one()
        row.state, row.outcome_code = "suppressed", "scope_unavailable"
        row.payload_ciphertext = None
        row.finalized_at = worker._database_now(ambient)
        ambient.add(row)
        ambient.flush()
        health = _sample()
        assert (
            health["queued"] == before["queued"]
            and health["suppressed"] == before["suppressed"]
        )
        assert ambient.in_transaction()
        ambient.rollback()
    assert (
        _row(receipt).state == "queued" and _row(receipt).payload_ciphertext is not None
    )


def test_two_independent_cleaners_do_not_repeat_terminal_transition(monkeypatch):
    _, options, routing = _ready(monkeypatch)
    receipt = outbox.enqueue_sms(**options)
    _record(_event(routing))
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: worker.maintain_sms_secrets(), range(2)))
    assert all(result["status"] == "available" for result in results)
    assert sum(result["cleared"] for result in results) == 1
    assert _row(receipt).state == "suppressed"
    assert worker.maintain_sms_secrets()["cleared"] == 0


@pytest.mark.parametrize("failure", ["exception", "expanded", "negative", "boolean"])
def test_task_discards_malformed_private_metadata_and_never_returns_details(
    monkeypatch, failure
):
    health = {name: 0 for name in operations.COUNT_FIELDS} | {
        "status": "ok",
        "queue_timing": "unqualified",
    }
    cleanup = {"status": "available", "cleared": 0}
    if failure == "expanded":
        health["private"] = cleanup["private"] = "Synthetic secret"
    elif failure == "negative":
        health["queued"], cleanup["cleared"] = -1, -1
    elif failure == "boolean":
        health["queued"], cleanup["cleared"] = True, True

    def value(result):
        if failure == "exception":
            raise ValueError("Synthetic secret must not escape")
        return result

    monkeypatch.setattr(job, "runtime_health", lambda: value(health))
    monkeypatch.setattr(job, "maintain_sms_secrets", lambda: value(cleanup))
    logs = _capture(monkeypatch)
    assert job.perform() is None
    assert logs == [
        (
            ("Visit Prep SMS maintenance",),
            {
                "event_code": "visit_prep_sms_maintenance",
                "cleanup_status": "storage_unavailable",
                "status": "storage_unavailable",
            },
        )
    ]


def test_storage_failure_is_not_reported_as_empty_healthy_queue(monkeypatch):
    def unavailable(*args):
        raise ValueError("Synthetic SQL/private failure")

    monkeypatch.setattr(operations, "Session", unavailable)
    assert operations.runtime_health() == {"status": "storage_unavailable"}
    monkeypatch.setattr(
        job,
        "maintain_sms_secrets",
        lambda: {"status": "storage_unavailable", "cleared": 2},
    )
    logs = _capture(monkeypatch)
    assert job.perform() is None
    assert logs[0][1] == {
        "event_code": "visit_prep_sms_maintenance",
        "cleanup_status": "storage_unavailable",
        "cleared": 2,
        "status": "storage_unavailable",
    }
