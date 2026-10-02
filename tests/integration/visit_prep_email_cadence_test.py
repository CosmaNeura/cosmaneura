"""Dormant cadence enforcement on real owned rows; no live model or provider."""

from concurrent.futures import ThreadPoolExecutor
from copy import copy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from threading import Event
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

import app.services.visit_prep_current_timing as current
import app.services.visit_prep_email_worker as worker
from app.services.visit_prep_email_enqueue import enqueue_email_invitation
from app.services.visit_prep_email_worker import (
    EmailSubmissionClaim,
    claim_email_invitation,
    dispatch_email_claim,
    run_one_email,
)
from app.services.visit_preps import (
    reschedule_visit_prep,
    rotate_visit_prep_credential,
)

from activemodel.session_manager import global_session
from app.models.practice import Practice
from sqlalchemy import text
from sqlmodel import select

from tests.integration.visit_prep_contribution_lock_test import _observe_waiting_lock
from tests.services.visit_prep_delivery_model_test import _update
from tests.services.visit_prep_email_enqueue_test import _ready, _rows
from tests.services.visit_prep_email_worker_test import _events, _options, _queued
from tests.services.visit_preps_test import _lineage


def _command(fixture, generation=1):
    return {
        key: fixture[3][key]
        for key in (
            "owner_practice_member_id",
            "owner_doctor_id",
            "practice_id",
            "visit_prep_id",
        )
    } | {"expected_credential_generation": generation}


def _rescheduled(fixture, *, at, timezone_name="UTC", generation=1):
    _, schedules, _ = _lineage(fixture[1].id)
    changed = reschedule_visit_prep(
        **_command(fixture, generation),
        expected_schedule_generation=schedules[-1].generation,
        scheduled_at=at.astimezone(ZoneInfo(timezone_name)),
        timezone_name=timezone_name,
    )
    assert changed.access_token is not None
    fixture[3].update(
        access_token=changed.access_token,
        expected_credential_generation=generation + 1,
        operation_id=str(uuid4()),
    )
    return fixture


def _quiet_options(fixture):
    hour = datetime.now(UTC).hour
    return _options(fixture) | {"quiet_hours": (hour, (hour + 1) % 24)}


@pytest.mark.parametrize(
    "changes",
    [
        {"quiet_hours": None},
        {"minimum_channel_spacing": None},
        {"quiet_hours": [1, 2]},
        {"quiet_hours": (1, 1)},
        {"quiet_hours": (True, 2)},
        {"quiet_hours": (0, 24)},
        {"minimum_channel_spacing": 4},
        {"minimum_channel_spacing": timedelta(minutes=59)},
        {"minimum_channel_spacing": timedelta(hours=49)},
    ],
)
def test_missing_invalid_or_coerced_policy_never_claims(changes):
    fixture = _queued("cadence-invalid")
    calls = []
    assert (
        run_one_email(
            **(_options(fixture) | changes),
            transport=lambda invitation: calls.append(invitation) or "accepted",
        )
        == "disabled"
    )
    assert calls == []
    row = _rows(fixture)[0]
    assert row.state == "queued" and row.submitted_at is None
    assert row.payload_ciphertext is not None and len(_events(fixture)) == 1


def test_absent_policy_has_no_enabled_fallback():
    fixture = _queued("cadence-no-policy")
    options = _options(fixture)
    del options["quiet_hours"], options["minimum_channel_spacing"]
    assert claim_email_invitation(**options) == "disabled"
    assert _rows(fixture)[0].state == "queued"


@pytest.mark.parametrize("timezone_name", ["UTC", "America/New_York", "Asia/Kolkata"])
def test_quiet_hours_use_current_schedule_local_time_without_claim(timezone_name):
    fixture = _ready("cadence-local-quiet")
    fixture = _rescheduled(
        fixture, at=datetime.now(UTC) + timedelta(days=14), timezone_name=timezone_name
    )
    enqueue_email_invitation(**fixture[3])
    local_hour = datetime.now(ZoneInfo(timezone_name)).hour
    options = _options(fixture) | {"quiet_hours": (local_hour, (local_hour + 1) % 24)}
    before = _rows(fixture)[0]
    calls = []
    assert (
        run_one_email(
            **options,
            transport=lambda invitation: calls.append(invitation) or "accepted",
        )
        == "wait"
    )
    after = _rows(fixture)[0]
    assert calls == [] and after.state == "queued" and after.submitted_at is None
    assert after.operation_id == before.operation_id
    assert after.payload_ciphertext == before.payload_ciphertext
    assert len(_events(fixture)) == 1


def test_waited_invitation_retains_exact_intent_then_claims_once_when_permitted():
    fixture = _queued("cadence-wait-resume")
    before = _rows(fixture)[0]
    assert claim_email_invitation(**_quiet_options(fixture)) == "wait"
    calls = []
    assert (
        run_one_email(
            **_options(fixture),
            transport=lambda invitation: calls.append(invitation) or "accepted",
        )
        == "accepted"
    )
    row = _rows(fixture)[0]
    assert row.operation_id == before.operation_id and row.state == "accepted"
    assert len(calls) == 1 and len(_events(fixture)) == 3


def test_quiet_window_past_appointment_suppresses_without_claiming():
    fixture = _ready("cadence-no-window")
    fixture = _rescheduled(fixture, at=datetime.now(UTC) + timedelta(seconds=10))
    enqueue_email_invitation(**fixture[3])
    calls = []
    assert (
        run_one_email(
            **_quiet_options(fixture),
            transport=lambda invitation: calls.append(invitation) or "accepted",
        )
        == "suppressed"
    )
    row = _rows(fixture)[0]
    assert calls == [] and row.submitted_at is None and row.payload_ciphertext is None
    assert row.outcome_code == "obsolete_context"
    assert {event.event_type for event in _events(fixture)} == {
        "visit_prep.email_invitation_queued",
        "visit_prep.email_suppressed",
    }


def test_quiet_first_candidate_does_not_block_due_candidate_in_same_batch():
    first = _queued("cadence-quiet-first")
    second = _ready("cadence-due-second")
    now = datetime.now(UTC)
    # Etc/GMT uses inverted signs. A real +5-hour schedule has a different
    # local hour, so the exact SAME trusted policy permits the second row.
    second = _rescheduled(
        second, at=now + timedelta(days=14), timezone_name="Etc/GMT-5"
    )
    enqueue_email_invitation(**second[3])
    options = _quiet_options(first)
    sources = (first[3]["source_verifier"], second[3]["source_verifier"])
    options["source_verifier"] = lambda session, evidence, **kwargs: any(
        source(session, evidence, **kwargs) for source in sources
    )
    options["settings"] = replace(
        options["settings"],
        enabled_doctor_ids=frozenset(str(f[0].doctor.id) for f in (first, second)),
    )
    calls = []
    assert (
        run_one_email(
            **options,
            transport=lambda invitation: calls.append(invitation.operation_id)
            or "accepted",
        )
        == "accepted"
    )
    assert calls == [second[3]["operation_id"]]
    assert _rows(first)[0].state == "queued" and len(_events(first)) == 1
    assert _rows(second)[0].state == "accepted"


@pytest.mark.parametrize(
    "changes",
    [
        {"quiet_hours": None},
        {"minimum_channel_spacing": None},
        {"minimum_channel_spacing": timedelta(hours=5)},
        {"quiet_hours": (1, 2)},
    ],
)
def test_policy_absent_or_changed_after_claim_suppresses_without_reopening(changes):
    fixture = _queued("cadence-policy-change")
    options = _options(fixture)
    # Ensure this valid alternative actually differs at every wall-clock hour.
    if changes == {"quiet_hours": (1, 2)} and options["quiet_hours"] == (1, 2):
        changes = {"quiet_hours": (2, 3)}
    claim = claim_email_invitation(**options)
    assert isinstance(claim, EmailSubmissionClaim)
    previous = (copy(claim), replace(claim))
    calls = []
    assert (
        dispatch_email_claim(
            claim,
            **(options | changes),
            transport=lambda invitation: calls.append(invitation) or "accepted",
        )
        == "suppressed"
    )
    row = _rows(fixture)[0]
    assert calls == [] and row.submitted_at == claim.submitted_at
    assert row.state == "suppressed" and row.payload_ciphertext is None
    for duplicate in previous:
        assert (
            dispatch_email_claim(duplicate, **options, transport=lambda _: "accepted")
            == "discarded"
        )
    assert run_one_email(**options, transport=lambda _: "accepted") == "idle"


@pytest.mark.parametrize(
    "state", ["accepted", "failed", "suppressed", "submitting", "uncertain"]
)
def test_old_generation_possible_submission_still_blocks_current_claim(state):
    fixture = _queued("cadence-prior-submission")
    options = _options(fixture)
    prior = claim_email_invitation(**options)
    assert isinstance(prior, EmailSubmissionClaim)
    if state != "submitting":
        _update(
            _rows(fixture)[0],
            state=state,
            outcome_code={
                "accepted": None,
                "failed": "message_invalid",
                "suppressed": "scope_unavailable",
                "uncertain": "submission_uncertain",
            }[state],
            payload_ciphertext=None,
            lease_until=None,
            finalized_at=datetime.now(UTC),
        )
    rotated = rotate_visit_prep_credential(**_command(fixture))
    assert rotated.access_token is not None
    fixture[3].update(
        access_token=rotated.access_token,
        expected_credential_generation=2,
        operation_id=str(uuid4()),
    )
    enqueue_email_invitation(**fixture[3])
    calls = []
    assert (
        run_one_email(
            **options,
            transport=lambda invitation: calls.append(invitation) or "accepted",
        )
        == "wait"
    )
    rows = sorted(_rows(fixture), key=lambda row: row.credential_generation)
    assert calls == [] and rows[0].state == state and rows[1].state == "queued"
    assert rows[1].submitted_at is None


def test_obsolete_unsubmitted_generation_is_not_excluded_as_current_intent():
    fixture = _queued("cadence-obsolete-queued")
    rotated = rotate_visit_prep_credential(**_command(fixture))
    assert rotated.access_token is not None
    fixture[3].update(
        access_token=rotated.access_token,
        expected_credential_generation=2,
        operation_id=str(uuid4()),
    )
    enqueue_email_invitation(**fixture[3])
    calls = []
    # First drain safely suppresses the obsolete queued bearer. The next drain
    # can send the distinct current intent; old unsubmitted work imposes no spacing.
    assert (
        run_one_email(**_options(fixture), transport=lambda _: "accepted")
        == "suppressed"
    )
    assert (
        run_one_email(
            **_options(fixture),
            transport=lambda invitation: calls.append(invitation) or "accepted",
        )
        == "accepted"
    )
    assert len(calls) == 1 and calls[0].access_token == rotated.access_token


@pytest.mark.parametrize("stage", ["claim", "dispatch"])
def test_real_appointment_expiry_while_waiting_on_practice_fence_prevents_effect(
    monkeypatch, stage
):
    fixture = _ready("cadence-appointment-lock")
    fixture = _rescheduled(fixture, at=datetime.now(UTC) + timedelta(seconds=3))
    enqueue_email_invitation(**fixture[3])
    options = _options(fixture)
    claim = claim_email_invitation(**options) if stage == "dispatch" else None
    if stage == "dispatch":
        assert isinstance(claim, EmailSubmissionClaim)
    started, calls = Event(), []
    original = worker._lock_rows

    def marked(session, identifier):
        session.execute(text("SET LOCAL application_name = 'vp_email_cadence_wait'"))
        started.set()
        return original(session, identifier)

    monkeypatch.setattr(worker, "_lock_rows", marked)

    def run():
        def transport(invitation):
            calls.append(invitation)
            return "accepted"

        if stage == "dispatch":
            assert isinstance(claim, EmailSubmissionClaim)
            return dispatch_email_claim(claim, **options, transport=transport)
        return run_one_email(**options, transport=transport)

    with global_session() as parent:
        parent.exec(
            select(Practice)
            .where(Practice.id == fixture[0].practice.id)
            .with_for_update()
        ).one()
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(run)
            try:
                assert started.wait(timeout=5)
                _observe_waiting_lock(parent, "vp_email_cadence_wait")
                parent.execute(text("SELECT pg_sleep(3.1)"))
            finally:
                parent.commit()
            assert future.result(timeout=10) == "suppressed"
    assert calls == [] and _rows(fixture)[0].payload_ciphertext is None


def test_final_claim_clock_after_real_audit_flush_rolls_back_claim(monkeypatch):
    fixture = _ready("cadence-final-claim")
    fixture = _rescheduled(fixture, at=datetime.now(UTC) + timedelta(seconds=3))
    enqueue_email_invitation(**fixture[3])
    original = worker._audit

    def delayed(session, prep, row, outcome):
        original(session, prep, row, outcome)
        if outcome == "submission_claimed":
            session.flush()
            session.execute(text("SELECT pg_sleep(3.1)"))

    monkeypatch.setattr(worker, "_audit", delayed)
    calls = []
    assert (
        run_one_email(
            **_options(fixture),
            transport=lambda invitation: calls.append(invitation) or "accepted",
        )
        == "wait"
    )
    row = _rows(fixture)[0]
    assert calls == [] and row.state == "queued" and row.submitted_at is None
    assert len(_events(fixture)) == 1


@pytest.mark.parametrize("stage", ["claim", "dispatch"])
def test_authority_expiry_after_actual_history_read_denies_effect(monkeypatch, stage):
    fixture = _queued(
        "cadence-source-after-query", expiry=datetime.now(UTC) + timedelta(seconds=2)
    )
    options = _options(fixture)
    claim = claim_email_invitation(**options) if stage == "dispatch" else None
    if stage == "dispatch":
        assert isinstance(claim, EmailSubmissionClaim)
    original = current._email_history

    def delayed(session, prep):
        history = original(session, prep)
        session.execute(text("SELECT pg_sleep(2.1)"))
        return history

    monkeypatch.setattr(current, "_email_history", delayed)
    calls = []

    def transport(invitation):
        calls.append(invitation)
        return "accepted"

    result = (
        dispatch_email_claim(claim, **options, transport=transport)
        if isinstance(claim, EmailSubmissionClaim)
        else run_one_email(**options, transport=transport)
    )
    assert result == "suppressed"
    assert calls == [] and _rows(fixture)[0].payload_ciphertext is None


def test_dispatch_rechecks_quiet_window_after_committed_claim(monkeypatch):
    fixture = _queued("cadence-dispatch-quiet")
    options = _options(fixture)
    claim = claim_email_invitation(**options)
    assert isinstance(claim, EmailSubmissionClaim)
    original = worker._cadence_decision

    def advanced(plan, *, policy, checked_at):
        # Bounded clock diagnostic: pure policy evaluation at a real UTC quiet
        # boundary, while actual custody/authority uses the unchanged DB clock.
        quiet_hour = policy[0][0]
        boundary = checked_at.replace(
            hour=quiet_hour, minute=0, second=0, microsecond=0
        )
        if boundary <= checked_at:
            boundary += timedelta(days=1)
        return original(plan, policy=policy, checked_at=boundary)

    monkeypatch.setattr(worker, "_cadence_decision", advanced)
    calls = []
    assert (
        dispatch_email_claim(
            claim,
            **options,
            transport=lambda invitation: calls.append(invitation) or "accepted",
        )
        == "suppressed"
    )
    row = _rows(fixture)[0]
    assert calls == [] and row.submitted_at == claim.submitted_at
    assert row.state == "suppressed" and row.payload_ciphertext is None
    assert (
        dispatch_email_claim(claim, **options, transport=lambda _: "accepted")
        == "discarded"
    )


def test_final_claim_rechecks_quiet_boundary_without_retaining_claim_audit(monkeypatch):
    fixture = _queued("cadence-final-quiet")
    original = worker._cadence_decision
    calls = 0

    def advanced(plan, *, policy, checked_at):
        nonlocal calls
        calls += 1
        if calls == 2:
            boundary = checked_at.replace(
                hour=policy[0][0], minute=0, second=0, microsecond=0
            )
            if boundary <= checked_at:
                boundary += timedelta(days=1)
            checked_at = boundary
        return original(plan, policy=policy, checked_at=checked_at)

    monkeypatch.setattr(worker, "_cadence_decision", advanced)
    assert claim_email_invitation(**_options(fixture)) == "wait"
    row = _rows(fixture)[0]
    assert calls == 2 and row.state == "queued" and row.submitted_at is None
    assert len(_events(fixture)) == 1


def test_current_history_failure_never_becomes_empty_history(monkeypatch):
    fixture = _queued("cadence-history-failure")

    def broken(*_args, **_kwargs):
        raise RuntimeError(fixture[3]["access_token"])

    monkeypatch.setattr(current, "_email_history", broken)
    calls = []
    assert (
        run_one_email(
            **_options(fixture),
            transport=lambda invitation: calls.append(invitation) or "accepted",
        )
        == "storage_unavailable"
    )
    assert calls == [] and _rows(fixture)[0].state == "queued"


@pytest.mark.parametrize("stage", ["claim", "dispatch"])
@pytest.mark.parametrize("damage", ["missing", "duplicate", "state", "timestamp"])
def test_exact_current_intent_must_be_present_before_self_exclusion(
    monkeypatch, stage, damage
):
    fixture = _queued("cadence-self-mismatch")
    options = _options(fixture)
    claim = claim_email_invitation(**options) if stage == "dispatch" else None
    if stage == "dispatch":
        assert isinstance(claim, EmailSubmissionClaim)
    original = worker.plan_locked_current_delivery

    def damaged(session, **kwargs):
        plan = original(session, **kwargs)
        (touch,) = plan.email_history
        history = {
            "missing": (),
            "duplicate": (touch, touch),
            "state": (replace(touch, state="suppressed"),),
            "timestamp": (
                replace(touch, requested_at=touch.requested_at - timedelta(seconds=1)),
            ),
        }[damage]
        return replace(plan, email_history=history)

    monkeypatch.setattr(worker, "plan_locked_current_delivery", damaged)
    calls = []

    def transport(invitation):
        calls.append(invitation)
        return "accepted"

    result = (
        dispatch_email_claim(claim, **options, transport=transport)
        if isinstance(claim, EmailSubmissionClaim)
        else run_one_email(**options, transport=transport)
    )
    assert result == "suppressed" and calls == []
    assert _rows(fixture)[0].payload_ciphertext is None


def test_cadence_queries_do_not_decode_other_invitation_envelopes(monkeypatch):
    fixture = _queued("cadence-history-private")
    old = _rows(fixture)[0]
    _update(
        old,
        state="suppressed",
        outcome_code="scope_unavailable",
        payload_ciphertext=None,
        finalized_at=datetime.now(UTC),
    )
    rotated = rotate_visit_prep_credential(**_command(fixture))
    assert rotated.access_token is not None
    fixture[3].update(
        access_token=rotated.access_token,
        expected_credential_generation=2,
        operation_id=str(uuid4()),
    )
    enqueue_email_invitation(**fixture[3])
    original = worker._authorization
    decoded = []

    def record(row, prep):
        decoded.append(row.credential_generation)
        return original(row, prep)

    monkeypatch.setattr(worker, "_authorization", record)
    assert (
        run_one_email(**_options(fixture), transport=lambda _: "accepted") == "accepted"
    )
    assert decoded == [2, 2]
