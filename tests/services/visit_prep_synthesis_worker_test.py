"""Real Core persistence with synthetic model transport; no external requests."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from typeid import TypeID

import app.configuration.visit_prep as configuration
import app.services.visit_prep_synthesis_operations as operations
import app.services.visit_prep_synthesis_publication as publication
import app.services.visit_prep_synthesis_worker as worker
from app.configuration.visit_prep import VisitPrepSettings
from app.services import visit_prep_access
from app.services.visit_prep_interviews import (
    complete_interview,
    confirm_interview,
    report_wrong_recipient,
)
from app.services.visit_prep_synthesis_builds import (
    VisitPrepBuildDiscarded,
    claim_visit_prep_synthesis,
    fail_visit_prep_synthesis_claim,
)
from app.services.visit_prep_synthesis_operations import runtime_health
from app.services.visit_prep_synthesis_usage import (
    VisitPrepUsageUnavailable,
    begin_synthesis_usage,
    record_synthesis_usage,
)
from app.services.visit_prep_synthesis_worker import (
    publish_claimed_synthesis,
    run_one_build,
)
from app.services.visit_preps import rotate_visit_prep_credential
from app.utils.encryption import EncryptionError

from activemodel.session_manager import get_session
from app.models.doctor import Doctor

from tests.services.visit_prep_contribution_commands_test import _correct, _review
from tests.services.visit_prep_contribution_projection_test import _prepared
from tests.services.visit_prep_interviews_test import _fixture, _respond
from tests.services.visit_prep_packages_test import _scope
from tests.services.visit_prep_synthesis_builds_test import (
    _build,
    _change_build,
    _expire,
    _request,
)
from tests.services.visit_prep_synthesis_publication_test import (
    _audits,
    _publish,
    _rows,
    _selected,
    _view,
)
from tests.services.visit_prep_synthesis_test import _brief, _fake
from tests.services.visit_prep_synthesis_usage_test import _events, _started, _usage
from tests.services.visit_preps_test import _withdraw_seat


def _accounted(monkeypatch, label="worker-publish", **options):
    fixture, claim, selected = _started(monkeypatch, label, **options)
    intent_id = begin_synthesis_usage(claim, selected)
    record_synthesis_usage(intent_id, _usage())
    return fixture, claim, selected, intent_id


@pytest.mark.parametrize("role,complete", [("patient", True), ("caregiver", False)])
def test_worker_generates_records_and_publishes_one_current_chart_free_brief(
    monkeypatch, role, complete
):
    fixture = _prepared(monkeypatch, "worker-current", role=role, complete=complete)
    selected = _selected(fixture)
    captured = _fake(monkeypatch, _brief(selected).model_dump_json())
    _request(fixture)
    assert run_one_build() == "ready"
    assert captured["request"]["store"] is False
    assert captured["options"]["max_retries"] == 0
    intent, result = _events(fixture)
    assert intent.status == "started" and result.status == "succeeded"
    assert result.call_intent_id == intent.id
    assert (result.input_tokens, result.output_tokens) == (200, 50)
    assert intent.person_id is intent.interaction_id is None
    row, view = _build(fixture), _view(fixture)
    assert row.state == "ready" and row.lease_until is row.failure_code is None
    assert row.attempts == 1 and len(_rows(fixture)) == 1
    assert view.status == "ready" and view.prepared == _brief(selected)
    assert view.package.preparation.patient_id is None
    assert view.package.contribution.actor_role == role
    assert view.package.contribution.current_answers_complete == complete
    assert _request(fixture).state == "ready"
    assert run_one_build() == "idle"
    assert len(_events(fixture)) == 2 and len(_rows(fixture)) == 1


def test_recorded_unavailable_tokens_are_not_a_missing_accounting_result(monkeypatch):
    fixture = _prepared(monkeypatch, "worker-usage-missing")
    selected = _selected(fixture)
    _fake(monkeypatch, _brief(selected).model_dump_json(), usage=False)
    _request(fixture)
    assert run_one_build() == "ready"
    result = _events(fixture)[1]
    assert result.status == "succeeded" and result.usage_availability == "unavailable"
    assert result.input_tokens is result.output_tokens is None


def test_all_skipped_job_needs_no_model_or_usage_and_publishes_empty_brief(monkeypatch):
    fixture = _fixture(monkeypatch, "worker-empty")
    confirm_interview(
        access_token=fixture.access_token, first_name="José", role="patient"
    )
    for key in ("reason", "medications", "adherence", "story"):
        _respond(fixture, key)
    complete_interview(access_token=fixture.access_token)
    _request(fixture)
    monkeypatch.setattr(
        worker,
        "begin_synthesis_usage",
        lambda *_args: pytest.fail("empty input created usage"),
    )
    assert run_one_build() == "ready"
    assert not _events(fixture)
    assert not _view(fixture).prepared.items() and _build(fixture).state == "ready"


@pytest.mark.parametrize("failure", ["refusal", "truncation", "invalid", "sdk"])
def test_failed_model_attempt_records_usage_then_backs_off_without_artifact(
    monkeypatch, failure
):
    fixture = _prepared(monkeypatch, "worker-model-failure")
    selected = _selected(fixture)
    options = {
        "refusal": {"refusal": "Synthetic refusal"},
        "truncation": {"finish_reason": "length"},
        "invalid": {},
        "sdk": {"error": RuntimeError("Synthetic source must not escape")},
    }[failure]
    captured = _fake(
        monkeypatch,
        "not json" if failure == "invalid" else _brief(selected).model_dump_json(),
        **options,
    )
    _request(fixture)
    before = datetime.now(UTC)
    assert run_one_build() == "generation_failed"
    assert captured["request"]
    intent, result = _events(fixture)
    assert result.status == "failed" and result.call_intent_id == intent.id
    if failure == "sdk":
        assert result.usage_availability == "unavailable"
    row = _build(fixture)
    assert row.state == "pending" and row.attempts == 1 and row.retry_at > before
    assert row.failure_code == "generation_failed" and row.lease_until is None
    assert not _rows(fixture) and not _audits(fixture)
    assert run_one_build() == "idle"


def test_three_generation_attempts_exhaust_without_an_automatic_fourth(monkeypatch):
    fixture = _prepared(monkeypatch, "worker-bounded")
    _fake(monkeypatch, "not json")
    _request(fixture)
    for attempt in range(1, 4):
        assert run_one_build() == "generation_failed"
        assert _build(fixture).attempts == attempt
        if attempt < 3:
            _change_build(fixture, retry_at=datetime.now(UTC) - timedelta(seconds=1))
    assert _build(fixture).state == "failed"
    assert len(_events(fixture)) == 6 and run_one_build() == "idle"
    assert not _rows(fixture)


@pytest.mark.parametrize("failure", ["intent", "result", "no_callback"])
def test_accounting_failure_never_publishes_or_blindly_retries(monkeypatch, failure):
    fixture = _prepared(monkeypatch, "worker-accounting-failure")
    selected = _selected(fixture)
    captured = _fake(monkeypatch, _brief(selected).model_dump_json())
    _request(fixture)

    def fail(*_args):
        raise RuntimeError("Synthetic sensitive accounting exception")

    if failure == "intent":
        monkeypatch.setattr(worker, "begin_synthesis_usage", fail)
    elif failure == "result":
        monkeypatch.setattr(worker, "record_synthesis_usage", fail)
    else:
        monkeypatch.setattr(
            worker, "generate_patient_brief", lambda *_args, **_kwargs: _brief(selected)
        )
    if failure == "intent":
        # Raw storage exceptions are handled by the job wrapper; the live lease
        # remains recoverable. A committed usage intent is still a prerequisite.
        with pytest.raises(RuntimeError):
            run_one_build()
        assert "request" not in captured and not _events(fixture)
        assert _build(fixture).state == "running"
    else:
        assert run_one_build() == "usage_unavailable"
        assert _build(fixture).state == "failed"
        assert len(_events(fixture)) == 1
        assert run_one_build() == "idle"
    assert not _rows(fixture) and not _audits(fixture)


def test_a_fake_successful_recorder_cannot_bypass_the_durable_publication_proof(
    monkeypatch,
):
    fixture = _prepared(monkeypatch, "worker-fake-recorder")
    _fake(monkeypatch, _brief(_selected(fixture)).model_dump_json())
    _request(fixture)
    monkeypatch.setattr(worker, "record_synthesis_usage", lambda *_args: None)
    assert run_one_build() == "usage_unavailable"
    assert len(_events(fixture)) == 1 and not _rows(fixture)


@pytest.mark.parametrize("proof", ["none", "unknown", "unmatched", "failed", "foreign"])
def test_publication_requires_a_successful_result_for_this_exact_claim(
    monkeypatch, proof
):
    fixture, claim, selected = _started(monkeypatch, "worker-proof")
    intent_id = None
    if proof == "unknown":
        intent_id = TypeID("model_usage")
    elif proof in {"unmatched", "failed"}:
        intent_id = begin_synthesis_usage(claim, selected)
        if proof == "failed":
            record_synthesis_usage(intent_id, _usage(status="failed"))
    elif proof == "foreign":
        other, _, _, intent_id = _accounted(monkeypatch, "worker-proof-other")
        monkeypatch.setattr(
            visit_prep_access,
            "VISIT_PREP_SETTINGS",
            VisitPrepSettings(
                enabled=True,
                enabled_doctor_ids=frozenset(
                    {str(fixture.doctor.id), str(other.doctor.id)}
                ),
            ),
        )
    with pytest.raises(VisitPrepUsageUnavailable):
        publish_claimed_synthesis(
            claim, selected, _brief(selected), intent_id=intent_id
        )
    assert not _rows(fixture) and _build(fixture).state == "running"


def test_publication_replays_a_prior_valid_artifact_and_atomically_finishes_job(
    monkeypatch,
):
    fixture, claim, selected, intent_id = _accounted(monkeypatch)
    first = _publish(fixture)
    ciphertext = _rows(fixture)[0].payload_ciphertext
    result = publish_claimed_synthesis(
        claim,
        selected,
        _brief(selected).model_copy(update={"visit_priorities": []}),
        intent_id=intent_id,
    )
    assert result.disposition == "replayed" and result.artifact_id == first.artifact_id
    assert _build(fixture).state == "ready" and len(_rows(fixture)) == 1
    assert _rows(fixture)[0].payload_ciphertext == ciphertext
    with pytest.raises(VisitPrepBuildDiscarded):
        publish_claimed_synthesis(
            claim, selected, _brief(selected), intent_id=intent_id
        )


@pytest.mark.parametrize("change", ["correction", "review", "rotation"])
def test_source_change_during_generation_records_old_usage_but_requeues_current_sources(
    monkeypatch, change
):
    fixture = _prepared(
        monkeypatch, "worker-inflight-source", complete=change != "rotation"
    )
    selected = _selected(fixture)
    _request(fixture)
    original = worker.generate_patient_brief
    _fake(monkeypatch, _brief(selected).model_dump_json())

    def changed(selection, **kwargs):
        if change == "correction":
            _correct(fixture)
        elif change == "review":
            _review(fixture)
        else:
            rotate_visit_prep_credential(
                **_scope(fixture, package=False),
                visit_prep_id=fixture.prep_id,
                expected_credential_generation=1,
            )
        return original(selection, **kwargs)

    monkeypatch.setattr(worker, "generate_patient_brief", changed)
    assert run_one_build() == "superseded"
    row = _build(fixture)
    assert row.state == "pending" and row.epoch == 2 and row.attempts == 0
    assert (
        row.source_version
        == _selected(fixture).source_version
        != selected.source_version
    )
    assert len(_events(fixture)) == 2 and _events(fixture)[1].status == "succeeded"
    assert not _rows(fixture) and not _audits(fixture)


@pytest.mark.parametrize("denial", ["baa", "seat", "identity", "quarantine"])
def test_inflight_authority_denial_retains_usage_but_prevents_publication(
    monkeypatch, denial
):
    fixture = _prepared(monkeypatch, "worker-inflight-denial")
    selected = _selected(fixture)
    _request(fixture)
    original = worker.generate_patient_brief
    _fake(monkeypatch, _brief(selected).model_dump_json())

    def changed(selection, **kwargs):
        if denial == "baa":
            fixture.practice.negotiated_baa_on_file = False
            fixture.practice.save()
        elif denial == "seat":
            _withdraw_seat(fixture.owner)
        elif denial == "identity":
            with get_session() as session:
                row = session.get(Doctor, fixture.doctor.id)
                row.clerk_id = "user_synthetic_changed_identity"
                session.add(row)
                session.commit()
        else:
            report_wrong_recipient(access_token=fixture.access_token)
        return original(selection, **kwargs)

    monkeypatch.setattr(worker, "generate_patient_brief", changed)
    assert run_one_build() == "discarded"
    assert _build(fixture).state == "failed"
    assert _build(fixture).failure_code == "scope_unavailable"
    assert len(_events(fixture)) == 2 and not _rows(fixture)


@pytest.mark.parametrize("failure", ["encryption", "audit", "commit", "final_lease"])
def test_publication_rolls_back_artifact_ready_state_and_audit_together(
    monkeypatch, failure
):
    fixture, claim, selected, intent_id = _accounted(monkeypatch, "worker-rollback")

    def fail(*_args, **_kwargs):
        raise EncryptionError()

    if failure == "encryption":
        monkeypatch.setattr(publication, "encrypt_synthesis_payload", fail)
    elif failure == "audit":
        monkeypatch.setattr(worker, "_audit", fail)
    elif failure == "commit":
        monkeypatch.setattr(worker, "_commit", fail)
    else:
        original = worker._audit

        def audit_then_expire(*args, **kwargs):
            original(*args, **kwargs)
            monkeypatch.setattr(
                worker, "_database_now", lambda _session: claim.lease_until
            )

        monkeypatch.setattr(worker, "_audit", audit_then_expire)
    with pytest.raises(
        VisitPrepBuildDiscarded if failure == "final_lease" else EncryptionError
    ):
        publish_claimed_synthesis(
            claim, selected, _brief(selected), intent_id=intent_id
        )
    assert not _rows(fixture) and not _audits(fixture)
    assert (
        _build(fixture).state == "running"
        and _build(fixture).lease_until == claim.lease_until
    )
    assert len(_events(fixture)) == 2


def test_unmatched_expired_intent_is_uncertain_and_late_result_resolves_only_accounting(
    monkeypatch,
):
    fixture, claim, selected = _started(monkeypatch, "worker-uncertain")
    intent_id = begin_synthesis_usage(claim, selected)
    monkeypatch.setattr(operations, "_database_now", lambda _session: claim.lease_until)
    before = runtime_health()
    assert before["uncertain_usage_attempts"] == 1 and before["status"] == "degraded"
    assert str(fixture.prep_id) not in repr(
        before
    ) and fixture.doctor.clerk_id not in repr(before)
    record_synthesis_usage(intent_id, _usage(status="failed"))
    assert runtime_health()["uncertain_usage_attempts"] == 0
    assert _build(fixture).state == "running" and not _rows(fixture)


def test_runtime_health_uses_database_time_and_current_profile_queue_counts(
    monkeypatch,
):
    baseline = runtime_health()
    fixture = _prepared(monkeypatch, "worker-health")
    _request(fixture)
    now = datetime.now(UTC)
    _change_build(fixture, requested_at=now - timedelta(seconds=121))
    monkeypatch.setattr(operations, "_database_now", lambda _session: now)
    health = runtime_health()
    assert health == {
        "pending": 1,
        "running": 0,
        "ready": baseline["ready"],
        "failed": baseline["failed"],
        "oldest_pending_seconds": 121,
        "uncertain_usage_attempts": 0,
        "status": "degraded",
    }
    _change_build(fixture, state="failed", failure_code="usage_unavailable")
    assert runtime_health()["failed"] == baseline["failed"] + 1
    assert runtime_health()["oldest_pending_seconds"] == 0


def test_crashed_attempt_keeps_intent_uncertain_while_next_attempt_can_finish(
    monkeypatch,
):
    baseline = runtime_health()
    fixture, first, selected = _started(monkeypatch, "worker-crash-recovery")
    begin_synthesis_usage(first, selected)
    _expire(fixture)
    _fake(monkeypatch, _brief(selected).model_dump_json())
    assert run_one_build() == "ready"
    events = _events(fixture)
    assert len(events) == 3
    assert [(event.batch_phase, event.build_attempt) for event in events] == [
        ("intent", 1),
        ("intent", 2),
        ("result", 2),
    ]
    # Advancing only the health clock makes the crashed original lease due.
    monkeypatch.setattr(operations, "_database_now", lambda _session: first.lease_until)
    health = runtime_health()
    assert health["ready"] == baseline["ready"] + 1
    assert health["uncertain_usage_attempts"] == 1
    assert health["status"] == "degraded"
    assert len(_rows(fixture)) == 1 and events[0].completed_at is None
    assert run_one_build() == "idle"


def test_time_dependent_entitlement_expiry_after_audit_flush_rolls_back_publication(
    monkeypatch,
):
    fixture, claim, selected, intent_id = _accounted(monkeypatch, "worker-final-access")
    future = claim.lease_until - timedelta(seconds=1)
    fixture.practice.billing_exempt_until = future - timedelta(seconds=1)
    fixture.practice.save()
    original = worker._audit

    def audit_then_expire(*args, **kwargs):
        original(*args, **kwargs)
        monkeypatch.setattr(worker, "_database_now", lambda _session: future)

    monkeypatch.setattr(worker, "_audit", audit_then_expire)
    from app.services.visit_preps import VisitPrepNotFound

    with pytest.raises(VisitPrepNotFound):
        publish_claimed_synthesis(
            claim, selected, _brief(selected), intent_id=intent_id
        )
    assert not _rows(fixture) and not _audits(fixture)
    assert _build(fixture).state == "running" and len(_events(fixture)) == 2


def test_expired_attempt_keeps_uncertain_usage_and_does_not_publish_over_new_claim(
    monkeypatch,
):
    fixture, claim, selected = _started(monkeypatch, "worker-old-lease")
    intent_id = begin_synthesis_usage(claim, selected)
    _expire(fixture)
    next_claim = claim_visit_prep_synthesis()
    assert next_claim.attempt == 2
    record_synthesis_usage(intent_id, _usage())
    with pytest.raises(VisitPrepBuildDiscarded):
        publish_claimed_synthesis(
            claim, selected, _brief(selected), intent_id=intent_id
        )
    assert not fail_visit_prep_synthesis_claim(claim, code="generation_failed")
    assert _build(fixture).attempts == 2 and _build(fixture).state == "running"
    assert not _rows(fixture) and len(_events(fixture)) == 2


@pytest.mark.parametrize("invalid", [False, True])
def test_registered_jobs_remain_dark_and_do_not_probe_storage(monkeypatch, invalid):
    import app.jobs.visit_prep_synthesis as job
    from app.celery import celery_app

    monkeypatch.setattr(
        configuration,
        "VISIT_PREP_SETTINGS",
        VisitPrepSettings(enabled=invalid, configuration_valid=not invalid),
    )
    monkeypatch.setattr(job, "run_one_build", lambda: pytest.fail("dark worker ran"))
    monkeypatch.setattr(job, "runtime_health", lambda: pytest.fail("dark health ran"))
    assert job.perform() is job.maintain() is None
    assert (
        job.perform_celery.autoretry_for == () and job.perform_celery.max_retries == 0
    )
    assert job.perform_celery.ignore_result and job.perform_celery.time_limit == 75
    assert celery_app.conf.beat_schedule["visit_prep_synthesis"] == {
        "task": "app.jobs.visit_prep_synthesis.perform",
        "schedule": 5.0,
    }
    assert celery_app.conf.beat_schedule["visit_prep_synthesis_maintenance"] == {
        "task": "app.jobs.visit_prep_synthesis.maintain",
        "schedule": 60.0,
    }


def test_job_sanitizes_storage_failure_and_does_not_return_patient_content(monkeypatch):
    import app.jobs.visit_prep_synthesis as job

    monkeypatch.setattr(
        configuration, "VISIT_PREP_SETTINGS", VisitPrepSettings(enabled=True)
    )
    logs = []
    monkeypatch.setattr(
        job,
        "log",
        SimpleNamespace(info=lambda *args, **kwargs: logs.append((args, kwargs))),
    )

    def fail():
        raise RuntimeError("Synthetic patient answer and secret must never be logged")

    monkeypatch.setattr(job, "run_one_build", fail)
    assert job.perform() is None
    assert logs == [
        (
            ("Visit Prep synthesis worker outcome",),
            {"event_code": "visit_prep_synthesis_build", "outcome": "storage_failed"},
        )
    ]
