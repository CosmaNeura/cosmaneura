"""Actual recurring queue planning with owned PostgreSQL and invented sources.

No real patient, source qualification, provider call or messaging activation.
"""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from threading import Event
from types import SimpleNamespace

import pytest

from app.celery import celery_app
from app.configuration import visit_prep, visit_prep_sms
from app.configuration.visit_prep_cadence import VisitPrepCadenceSettings
from app.jobs import visit_prep_sms_scheduler as job
from app.services import visit_prep_sms_outbox as outbox
from app.services import visit_prep_sms_policy as policy
from app.services import visit_prep_sms_scheduler as scheduler
from app.services import visit_prep_sms_worker as worker
from app.services.visit_prep_sms_suppression import sms_sender_digest

from activemodel.session_manager import get_engine
from app.models.visit_prep_delivery_link import VisitPrepDeliveryLink
from app.models.visit_prep_sms_delivery import VisitPrepSMSDelivery
from app.models.visit_prep_sms_scan import VisitPrepSMSScan
from sqlalchemy import event, text
from sqlmodel import Session, select

from tests.integration.visit_prep_sms_outbox_test import (
    _finish_queue_only_fixtures,  # noqa: F401 -- only owned newly created queues
)
from tests.integration.visit_prep_sms_outbox_test import (
    _ready as _sms_ready,
)
from tests.integration.visit_prep_sms_policy_test import (
    _approval,
    _intent,
    _with_durable_policy,
)
from tests.integration.visit_prep_sms_suppression_test import _event, _record
from tests.integration.visit_prep_sms_worker_test import _age_owned_row, _worker


def _ready(monkeypatch, *, scheduled_at=None):
    if scheduled_at is None:
        fixture, options, _ = _with_durable_policy(monkeypatch)
        source = options["approval_verifier"].sender_source_verifier
    else:
        fixture, options, routing = _sms_ready(monkeypatch, scheduled_at=scheduled_at)
        policy.record_sms_policy_decision(
            **_intent(fixture, options["settings"], routing)
        )
        approval = _approval(fixture, options["settings"])
        options = options | {"approval": approval}

        def source(session, *, sender_digest, checked_at):
            return (
                sender_digest == approval.sender_digest
                and checked_at >= approval.effective_at
            )

    settings = replace(options["settings"], sms_schedule_enabled=True)
    options = options | {
        "settings": settings,
        "approval_verifier": policy.CoreSMSApprovalVerifier(settings, source),
    }
    monkeypatch.setattr(visit_prep, "VISIT_PREP_SETTINGS", settings)
    monkeypatch.setattr(outbox, "current_standard_cadence", lambda: _awake_cadence())
    return fixture, options, source


def _awake_cadence():
    # Explicit synthetic timing, not a production quiet-hours override.
    hour = datetime.now(UTC).hour
    return VisitPrepCadenceSettings(
        "synthetic-scan-awake", (hour + 1) % 24, (hour + 2) % 24, 4
    )


def _run(options, source):
    return scheduler.schedule_sms_batch(
        patient_origin=options["patient_origin"],
        sender_source_verifier=source,
        consent_verifier=options["consent_verifier"],
    )


def _stored(fixture):
    with Session(get_engine()) as session:
        queues = session.exec(
            select(VisitPrepSMSDelivery).where(
                VisitPrepSMSDelivery.visit_prep_id == fixture.prep_id
            )
        ).all()
        links = session.exec(
            select(VisitPrepDeliveryLink).where(
                VisitPrepDeliveryLink.visit_prep_id == fixture.prep_id
            )
        ).all()
        return queues, links


def _checkpoint():
    with Session(get_engine()) as session:
        return session.get(VisitPrepSMSScan, scheduler.SCAN_NAME)


def test_real_queue_and_worker_then_repeat_do_not_mint_or_send_again(monkeypatch):
    fixture, options, source = _ready(monkeypatch)
    result = _run(options, source)
    assert result == {
        "status": "planned",
        "scanned": 1,
        "queued": 1,
        "waiting": 0,
        "omitted": 0,
        "unavailable": 0,
    }
    queues, links = _stored(fixture)
    assert len(queues) == len(links) == 1
    assert queues[0].operation_id == links[0].operation_id
    assert queues[0].expires_at <= links[0].expires_at
    assert _checkpoint().after_id is None
    assert _run(options, source)["queued"] == 0
    assert len(_stored(fixture)[1]) == 1
    seen = []

    def synthetic_transport(message):
        seen.append(message.step)
        assert message.access_token != fixture.access_token
        return "accepted"

    assert (
        worker.run_one_sms(**_worker(options, transport=synthetic_transport))
        == "accepted"
    )
    assert seen == ["booking_sms"]
    assert _run(options, source)["queued"] == 0
    assert len(_stored(fixture)[0]) == len(_stored(fixture)[1]) == 1
    assert _stored(fixture)[0][0].payload_ciphertext is None


@pytest.mark.parametrize("missing", ["schedule", "sender", "consent", "malformed"])
def test_default_off_or_missing_sources_touch_no_database(monkeypatch, missing):
    _, options, source = _ready(monkeypatch)
    if missing == "schedule":
        monkeypatch.setattr(
            visit_prep,
            "VISIT_PREP_SETTINGS",
            replace(options["settings"], sms_schedule_enabled=False),
        )
    elif missing == "sender":
        source = None
    elif missing == "consent":
        options = options | {"consent_verifier": None}
    else:
        monkeypatch.setattr(visit_prep, "VISIT_PREP_SETTINGS", "true")
    monkeypatch.setattr(
        scheduler,
        "get_engine",
        lambda: pytest.fail("Dormant planning must not open storage"),
    )
    assert _run(options, source) == {"status": "disabled"}


@pytest.mark.parametrize("denied", ["sender", "consent", "stop", "withdrawn", "cohort"])
def test_authority_denial_never_issues_link_or_queue(monkeypatch, denied):
    fixture, options, source = _ready(monkeypatch)
    if denied == "sender":

        def source(*args, **kw):
            return False
    elif denied == "consent":
        options = options | {"consent_verifier": lambda *args, **kw: False}
    elif denied == "stop":
        _record(_event(visit_prep_sms.ROUTING))
    elif denied == "withdrawn":
        policy.record_sms_policy_decision(
            **(
                _intent(fixture, options["settings"], visit_prep_sms.ROUTING)
                | {
                    "event_kind": "withdrawn",
                    "expected_event_sequence": 1,
                    "expires_at": None,
                }
            )
        )
    else:
        monkeypatch.setattr(
            visit_prep,
            "VISIT_PREP_SETTINGS",
            replace(
                options["settings"],
                enabled_doctor_ids=frozenset(),
                enabled_practice_ids=frozenset(),
            ),
        )
    result = _run(options, source)
    assert result["queued"] == 0
    assert not any(_stored(fixture))
    assert result["scanned"] == (0 if denied == "cohort" else 1)


def test_quiet_window_waits_without_retaining_a_link(monkeypatch):
    fixture, options, source = _ready(monkeypatch)
    hour = datetime.now(UTC).hour
    monkeypatch.setattr(
        outbox,
        "current_standard_cadence",
        lambda: VisitPrepCadenceSettings(
            "synthetic-scan-quiet", hour, (hour + 1) % 24, 4
        ),
    )
    assert _run(options, source)["waiting"] == 1
    assert not any(_stored(fixture))


def test_late_queue_failure_rolls_back_alias_and_advances_checkpoint(monkeypatch):
    fixture, options, source = _ready(monkeypatch)
    actual = scheduler.enqueue_linked_sms

    def fail_after_queue(**arguments):
        receipt = actual(**arguments)
        assert receipt.disposition == "queued"
        raise RuntimeError("synthetic failed handoff")

    monkeypatch.setattr(scheduler, "enqueue_linked_sms", fail_after_queue)
    assert _run(options, source)["unavailable"] == 1
    assert not any(_stored(fixture))
    version = _checkpoint().version
    monkeypatch.setattr(scheduler, "enqueue_linked_sms", actual)
    assert _run(options, source)["queued"] == 1
    assert _checkpoint().version == version + 1


def test_late_configuration_change_rolls_back_all_queue_link_and_cursor_work(
    monkeypatch,
):
    fixture, options, source = _ready(monkeypatch)
    before = _checkpoint()
    actual = scheduler.enqueue_linked_sms

    def disable_after_queue(**arguments):
        result = actual(**arguments)
        monkeypatch.setattr(
            visit_prep,
            "VISIT_PREP_SETTINGS",
            replace(options["settings"], sms_schedule_enabled=False),
        )
        return result

    monkeypatch.setattr(scheduler, "enqueue_linked_sms", disable_after_queue)
    assert _run(options, source) == {"status": "storage_unavailable"}
    assert not any(_stored(fixture))
    after = _checkpoint()
    assert (
        after is None and before is None
    ) or after.model_dump() == before.model_dump()


def test_commit_acknowledgment_loss_replay_uses_stored_queue_without_new_secret(
    monkeypatch,
):
    fixture, options, source = _ready(monkeypatch)
    actual_commit = Session.commit

    def lost_ack(session):
        actual_commit(session)
        raise RuntimeError("synthetic commit acknowledgment lost")

    with monkeypatch.context() as fault:
        fault.setattr(Session, "commit", lost_ack)
        assert _run(options, source) == {"status": "storage_unavailable"}
    assert len(_stored(fixture)[0]) == len(_stored(fixture)[1]) == 1
    assert _run(options, source)["queued"] == 0
    assert len(_stored(fixture)[1]) == 1


def test_concurrent_recurring_jobs_share_one_scan_and_one_actual_queue(monkeypatch):
    fixture, options, source = _ready(monkeypatch)
    entered, release = Event(), Event()
    actual_page = scheduler._page

    def held_page(*args):
        entered.set()
        assert release.wait(timeout=10)
        return actual_page(*args)

    monkeypatch.setattr(scheduler, "_page", held_page)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(_run, options, source)
        assert entered.wait(timeout=10)
        try:
            assert pool.submit(_run, options, source).result(timeout=10) == {
                "status": "busy"
            }
        finally:
            release.set()
        assert first.result(timeout=10)["queued"] == 1
    assert len(_stored(fixture)[0]) == len(_stored(fixture)[1]) == 1


def test_bounded_checkpoint_reaches_later_roots_after_unavailable_first_page(
    monkeypatch,
):
    first, options, _ = _ready(monkeypatch)
    routing = visit_prep_sms.ROUTING
    fixtures = [first]
    consent_sources = [options["consent_verifier"]]
    for _ in range(10):
        fixture, candidate, _ = _sms_ready(monkeypatch, routing=routing)
        policy.record_sms_policy_decision(
            **_intent(fixture, candidate["settings"], routing)
        )
        fixtures.append(fixture)
        consent_sources.append(candidate["consent_verifier"])
    settings = replace(
        options["settings"],
        enabled_doctor_ids=frozenset(str(f.doctor.id) for f in fixtures),
    )
    monkeypatch.setattr(visit_prep, "VISIT_PREP_SETTINGS", settings)
    ordered = sorted(fixtures, key=lambda f: f.prep_id.uuid)
    last = ordered[-1]
    last_consent_id = _last_consent_id(last)

    def only_last_consent(session, **kw):
        return kw["consent_event_id"] == last_consent_id and any(
            check(session, **kw) for check in consent_sources
        )

    def source(session, *, sender_digest, checked_at):
        return sender_digest == sms_sender_digest(routing)

    options = options | {"consent_verifier": only_last_consent}
    first_page = _run(options, source)
    assert first_page["scanned"] == first_page["unavailable"] == 10
    assert _checkpoint().after_id == ordered[9].prep_id.uuid
    second_page = _run(options, source)
    assert second_page["scanned"] == second_page["queued"] == 1
    assert _checkpoint().after_id is None
    assert len(_stored(last)[0]) == len(_stored(last)[1]) == 1


def _last_consent_id(fixture):
    from app.models.visit_prep_sms_consent import VisitPrepSMSConsentEvent

    with Session(get_engine()) as session:
        return str(
            session.exec(
                select(VisitPrepSMSConsentEvent).where(
                    VisitPrepSMSConsentEvent.visit_prep_id == fixture.prep_id
                )
            )
            .one()
            .id
        )


def test_job_is_dormant_registered_bounded_and_metadata_only(monkeypatch):
    logs = []
    monkeypatch.setattr(
        job, "log", SimpleNamespace(info=lambda *args, **kw: logs.append((args, kw)))
    )
    assert job.SENDER_SOURCE_VERIFIER is job.CONSENT_SOURCE_VERIFIER is None
    assert job.perform() is None and logs == []
    monkeypatch.setattr(
        job,
        "schedule_sms_batch",
        lambda **kw: {
            "status": "planned",
            "scanned": 1,
            "queued": 1,
            "waiting": 0,
            "omitted": 0,
            "unavailable": 0,
            "private": "synthetic secret",
        },
    )
    assert job.perform() is None
    assert logs == [
        (
            ("Visit Prep SMS planning",),
            {"event_code": "visit_prep_sms_planning", "status": "storage_unavailable"},
        )
    ]
    assert job.perform_celery.ignore_result and job.perform_celery.time_limit == 60
    assert not job.perform_celery.store_errors_even_if_ignored
    assert (
        job.perform_celery.autoretry_for == () and job.perform_celery.max_retries == 0
    )
    assert celery_app.conf.beat_schedule["visit_prep_sms_scheduler"] == {
        "task": job.perform_celery.name,
        "schedule": 60.0,
    }


def test_sql_storage_failure_cannot_be_reported_as_zero_work(monkeypatch):
    fixture, options, source = _ready(monkeypatch)

    def storage_fault(connection, cursor, statement, parameters, context, executemany):
        if "visit_prep_sms_scan" in statement:
            raise RuntimeError("synthetic storage failure")

    engine = get_engine()
    event.listen(engine, "before_cursor_execute", storage_fault)
    try:
        assert _run(options, source) == {"status": "storage_unavailable"}
    finally:
        event.remove(engine, "before_cursor_execute", storage_fault)
    assert not any(_stored(fixture))


def test_scheduler_configuration_is_a_strict_default_off_unit():
    assert visit_prep.load_visit_prep_settings({}).sms_schedule_enabled is False
    enabled = {
        "FF_VISIT_PREP_202608": "true",
        "VISIT_PREP_PUBLIC_API_ENABLED": "true",
        "VISIT_PREP_SMS_SEND_ENABLED": "true",
        "VISIT_PREP_SMS_SCHEDULE_ENABLED": "true",
    }
    assert visit_prep.load_visit_prep_settings(enabled).sms_schedule_enabled is True
    invalid = visit_prep.load_visit_prep_settings(
        enabled | {"VISIT_PREP_SMS_SCHEDULE_ENABLED": "perhaps"}
    )
    assert (
        invalid.configuration_valid is False
        and invalid.enabled is False
        and invalid.sms_send_enabled is False
        and invalid.sms_schedule_enabled is False
    )


def test_actual_twelve_hour_reminder_consumes_retained_booking_history(monkeypatch):
    fixture, options, source = _ready(
        monkeypatch, scheduled_at=datetime.now(UTC) + timedelta(hours=10)
    )
    # Explicit time-aging fixture in the runner's disposable synthetic DB only.
    # This does not qualify live scheduling over elapsed hours; all queue/link
    # issuance and submission guards remain enabled during actual operations.
    from tests.visit_prep.isolated_plugin import _owned_test_root

    _owned_test_root()
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
    assert _run(options, source)["queued"] == 1
    assert (
        worker.run_one_sms(**_worker(options, transport=lambda message: "accepted"))
        == "accepted"
    )
    booking = _stored(fixture)[0][0]
    _age_owned_row(
        booking,
        "requested_at=clock_timestamp()-interval '6 hours', submitted_at=clock_timestamp()-interval '5 hours', finalized_at=clock_timestamp()-interval '5 hours'",
    )
    assert _run(options, source)["queued"] == 1
    queues, links = _stored(fixture)
    assert {row.step for row in queues} == {"booking_sms", "unfinished_12h_sms"}
    assert len(links) == 2
    seen = []

    def synthetic_transport(message):
        seen.append(message.step)
        return "accepted"

    assert (
        worker.run_one_sms(**_worker(options, transport=synthetic_transport))
        == "accepted"
    )
    assert seen == ["unfinished_12h_sms"]
    assert _run(options, source)["queued"] == 0


def test_actual_slow_source_sql_rolls_back_root_and_resumes_after_timeout(monkeypatch):
    fixture, options, source = _ready(monkeypatch)

    def slow_source(session, **kw):
        # Owned local PostgreSQL only. The real per-root SQL budget cancels
        # this query; no mocked clock, provider or shared connection is used.
        session.execute(text("SELECT pg_sleep(4)"))
        return source(session, **kw)

    result = _run(options, slow_source)
    assert result["scanned"] == result["unavailable"] == 1
    assert result["queued"] == 0 and not any(_stored(fixture))
    assert _checkpoint().after_id is None
    assert _run(options, source)["queued"] == 1
