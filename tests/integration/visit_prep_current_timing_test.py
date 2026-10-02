"""Actual locked cadence evidence and timed handoff; invented consent only.

No provider, real contact, runtime job, enabled setting or SMS history is used.
Independent PostgreSQL connections exercise current changes and atomic retries.
"""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from threading import Barrier, Event
from uuid import uuid4

import pytest

from app.services.visit_prep_current_timing import plan_locked_current_delivery
from app.services.visit_prep_email_contract import VisitPrepEmailInvalid
from app.services.visit_prep_email_enqueue import enqueue_email_invitation
from app.services.visit_prep_email_worker import (
    EmailSubmissionClaim,
    claim_email_invitation,
    dispatch_email_claim,
)
from app.services.visit_prep_interviews import get_interview
from app.services.visit_prep_timed_email import (
    enqueue_locked_timed_email,
    enqueue_timed_email_invitation,
)
from app.services.visit_preps import (
    VisitPrepError,
    reconcile_visit_prep,
    reschedule_visit_prep,
    revoke_visit_prep,
    rotate_visit_prep_credential,
)

from activemodel.session_manager import get_session, global_session
from app.models.appointment import Appointment
from app.models.authority import AuthorityAuditEvent
from app.models.patient import Patient
from app.models.practice import Practice
from app.models.visit_prep import VisitPrepCredentialGeneration
from app.models.visit_prep_delivery import VisitPrepEmailDelivery
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, select

from tests.integration.visit_prep_contribution_lock_test import _observe_waiting_lock
from tests.services.visit_prep_delivery_model_test import _update
from tests.services.visit_prep_email_enqueue_test import _ready, _rows
from tests.services.visit_prep_email_worker_test import _options
from tests.services.visit_prep_interviews_test import _enable
from tests.services.visit_preps_test import _withdraw_seat


@pytest.fixture
def preparation():
    def create(label="current-timing"):
        return _ready(label)

    return create


def _policy(*, quiet=False):
    hour = datetime.now(UTC).hour
    return {
        "quiet_hours": (hour, (hour + 1) % 24)
        if quiet
        else ((hour + 1) % 24, (hour + 2) % 24),
        "minimum_channel_spacing": timedelta(hours=4),
    }


def _scope(fixture):
    return {
        key: fixture[3][key]
        for key in (
            "owner_practice_member_id",
            "owner_doctor_id",
            "practice_id",
            "expected_clerk_subject",
            "visit_prep_id",
            "settings",
        )
    }


def _plan(fixture, **changes):
    with get_session() as session:
        result = plan_locked_current_delivery(
            session, **(_scope(fixture) | _policy() | changes)
        )
        session.commit()
        return result


def _timed(fixture, *, quiet=False, **changes):
    return enqueue_timed_email_invitation(
        **(fixture[3] | _policy(quiet=quiet) | changes)
    )


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


def test_actual_current_plan_is_value_free_and_sms_history_is_not_invented(
    preparation, monkeypatch
):
    fixture = preparation()

    # The reader must not decrypt any private envelope or resolve a source.
    def forbidden(*_args, **_kwargs):
        raise AssertionError("private decryption is not a cadence read")

    monkeypatch.setattr("app.utils.encryption.decrypt_value", forbidden)
    plan = _plan(fixture)
    assert plan.snapshot.first_party_open_state == "unopened"
    assert plan.snapshot.schedule_generation == plan.snapshot.credential_generation == 1
    assert plan.email_history == () and plan.sms_history_available is False
    assert plan.decisions[0].action == "candidate"
    assert {d.reason for d in plan.decisions[1:]} == {"channel_history_unavailable"}
    assert all(
        d.action == "wait" and d.next_check_at is None for d in plan.decisions[1:]
    )
    assert _rows(fixture) == []
    assert repr(plan) == "CurrentTimingPlan(<redacted>)"
    private = (
        fixture[3]["access_token"],
        fixture[3]["expected_clerk_subject"],
        "patient@example.com",
        "José",
    )
    assert all(value not in str(asdict(plan)) for value in private)


def test_actual_first_party_open_rotation_and_reschedule_feed_current_plan(
    preparation, monkeypatch
):
    fixture = preparation()
    _enable(monkeypatch, fixture[0].doctor)
    get_interview(access_token=fixture[3]["access_token"])
    first = _plan(fixture)
    assert (
        first.snapshot.first_party_open_state == "opened"
        and first.snapshot.opened_at is not None
    )
    rotate_visit_prep_credential(**_command(fixture))
    reschedule_visit_prep(
        **_command(fixture, 2),
        expected_schedule_generation=1,
        scheduled_at=first.snapshot.scheduled_at + timedelta(days=3),
        timezone_name="UTC",
    )
    current = _plan(fixture)
    assert (
        current.snapshot.credential_generation == 3
        and current.snapshot.schedule_generation == 2
    )
    assert current.snapshot.opened_at == first.snapshot.opened_at
    assert current.decisions[
        2
    ].cadence_due_at == current.snapshot.scheduled_at - timedelta(hours=48)


def test_quiet_wait_creates_no_intent_then_due_handoff_preserves_exact_replay(
    preparation,
):
    fixture = preparation()
    wait = _timed(fixture, quiet=True)
    assert (
        wait.disposition == "wait" and wait.reason == "not_due" and wait.receipt is None
    )
    assert wait.next_check_at is not None and _rows(fixture) == []
    created = _timed(fixture)
    assert created.disposition == "queued" and created.receipt is not None
    replay = _timed(fixture, quiet=True, source_verifier=None)
    assert replay.disposition == "replayed" and replay.receipt is not None
    assert created.receipt.delivery_id == replay.receipt.delivery_id
    assert len(_rows(fixture)) == 1
    with pytest.raises(VisitPrepEmailInvalid):
        _timed(fixture, operation_id=str(uuid4()))
    assert len(_rows(fixture)) == 1


@pytest.mark.parametrize(
    "state", ["queued", "submitting", "accepted", "failed", "suppressed", "uncertain"]
)
def test_all_actual_email_states_and_old_generation_history_are_retained(
    preparation, state
):
    fixture = preparation()
    enqueue_email_invitation(**fixture[3])
    if state in {"submitting", "accepted", "uncertain"}:
        assert isinstance(
            claim_email_invitation(**_options(fixture)), EmailSubmissionClaim
        )
    if state not in {"queued", "submitting"}:
        _update(
            _rows(fixture)[0],
            state=state,
            outcome_code={
                "accepted": None,
                "failed": "message_invalid",
                "suppressed": "obsolete_context",
                "uncertain": "submission_uncertain",
            }[state],
            payload_ciphertext=None,
            lease_until=None,
            finalized_at=datetime.now(UTC),
        )
    before = _plan(fixture)
    assert len(before.email_history) == 1 and before.email_history[0].state == state
    assert before.decisions[0].reason == "already_planned"
    rotated = rotate_visit_prep_credential(**_command(fixture))
    reschedule_visit_prep(
        **_command(fixture, 2),
        expected_schedule_generation=1,
        scheduled_at=before.snapshot.scheduled_at + timedelta(days=2),
        timezone_name="UTC",
    )
    after = _plan(fixture)
    assert (
        after.snapshot.credential_generation == 3
        and after.snapshot.schedule_generation == 2
    )
    assert after.email_history == before.email_history
    assert after.decisions[0].reason == (
        "channel_pending"
        if state == "submitting"
        else "submission_uncertain"
        if state == "uncertain"
        else "not_due"
        if state == "accepted"
        else "due"
    )
    assert len(_rows(fixture)) == 1 and _rows(fixture)[0].state == state
    # A stale exact receipt remains recoverable, never a new current submission.
    replay = _timed(fixture, source_verifier=None)
    assert replay.disposition == "replayed" and replay.receipt is not None
    assert replay.receipt.credential_generation == 1
    assert rotated.access_token is not None


@pytest.mark.parametrize(
    "change", ["subject", "owner", "support", "baa", "disabled", "cohort", "consent"]
)
def test_denied_timed_handoff_never_records_new_work(preparation, change):
    fixture = preparation()
    changes = {}
    if change == "subject":
        changes["expected_clerk_subject"] = "wrong-synthetic-subject"
    elif change == "owner":
        changes["visit_prep_id"] = preparation("other-timing-owner")[1].id
    elif change == "support":
        changes["view_as_active"] = True
    elif change == "baa":
        fixture[0].practice.negotiated_baa_on_file = False
        fixture[0].practice.save()
    elif change == "consent":
        changes["source_verifier"] = None
    else:
        changes["settings"] = replace(
            fixture[3]["settings"],
            **(
                {"enabled": False}
                if change == "disabled"
                else {"enabled_doctor_ids": frozenset()}
            ),
        )
    with pytest.raises(VisitPrepEmailInvalid) as caught:
        _timed(fixture, **changes)
    assert caught.value.__cause__ is caught.value.__context__ is None
    assert _rows(fixture) == []


def test_inactive_plan_omits_email_and_channel_off_does_not_grant_handoff(preparation):
    fixture = preparation()
    plan = _plan(
        fixture,
        settings=replace(
            fixture[3]["settings"], public_api_enabled=False, email_send_enabled=False
        ),
    )
    assert plan.decisions[0].action == "candidate"  # advice is not authorization
    with pytest.raises(VisitPrepEmailInvalid):
        _timed(
            fixture, settings=replace(fixture[3]["settings"], email_send_enabled=False)
        )
    revoke_visit_prep(**_command(fixture), reason="appointment_cancelled")
    inactive = _plan(fixture)
    assert (
        inactive.snapshot.appointment_cancelled
        and inactive.decisions[0].reason == "inactive"
    )
    assert _timed(fixture).disposition == "omit" and _rows(fixture) == []


def test_caller_rollback_removes_due_outbox_and_audit(preparation):
    fixture = preparation()
    with get_session() as session:
        result = enqueue_locked_timed_email(session, **(fixture[3] | _policy()))
        assert result.disposition == "queued" and session.in_transaction()
        session.rollback()
    assert _rows(fixture) == []
    with get_session() as session:
        assert (
            session.exec(
                select(AuthorityAuditEvent).where(
                    AuthorityAuditEvent.event_type
                    == "visit_prep.email_invitation_queued",
                    AuthorityAuditEvent.client_event_id == fixture[3]["operation_id"],
                )
            ).all()
            == []
        )


@pytest.mark.parametrize("same_key", [True, False])
def test_two_timed_handoffs_serialize_one_actual_intent(preparation, same_key):
    fixture = preparation()
    barrier = Barrier(2)

    def handoff(index):
        barrier.wait(timeout=5)
        try:
            return _timed(
                fixture,
                operation_id=fixture[3]["operation_id"]
                if same_key or index == 0
                else str(uuid4()),
            )
        except VisitPrepEmailInvalid:
            return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        values = [
            future.result(timeout=10)
            for future in [executor.submit(handoff, index) for index in range(2)]
        ]
    assert len(_rows(fixture)) == 1
    if same_key:
        assert all(value is not None for value in values)
        assert {value.disposition for value in values if value is not None} == {
            "queued",
            "replayed",
        }
    else:
        assert values.count(None) == 1


@pytest.mark.parametrize("change", ["baa", "rotation", "reschedule", "cancel"])
def test_current_plan_observes_committed_change_after_real_wait(preparation, change):
    fixture = preparation()
    started = Event()

    def read():
        with global_session() as session:
            session.execute(
                text("SET LOCAL application_name = 'vp_current_timing_wait'")
            )
            started.set()
            return _plan(fixture)

    with global_session() as parent:
        parent.exec(
            select(Practice)
            .where(Practice.id == fixture[0].practice.id)
            .with_for_update()
        ).one()
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(read)
            try:
                assert started.wait(timeout=5)
                _observe_waiting_lock(parent, "vp_current_timing_wait")
                if change == "baa":
                    fixture[0].practice.negotiated_baa_on_file = False
                    fixture[0].practice.save()
                elif change == "rotation":
                    rotate_visit_prep_credential(**_command(fixture))
                elif change == "reschedule":
                    reschedule_visit_prep(
                        **_command(fixture),
                        expected_schedule_generation=1,
                        scheduled_at=datetime.now(UTC) + timedelta(days=30),
                        timezone_name="UTC",
                    )
                else:
                    revoke_visit_prep(
                        **_command(fixture), reason="appointment_cancelled"
                    )
            finally:
                parent.commit()
            if change == "baa":
                with pytest.raises((VisitPrepEmailInvalid, VisitPrepError)):
                    future.result(timeout=10)
            else:
                plan = future.result(timeout=10)
                assert plan.snapshot.credential_generation == (
                    1 if change == "cancel" else 2
                )
                assert plan.snapshot.schedule_generation == (
                    2 if change == "reschedule" else 1
                )
                assert (
                    (plan.decisions[0].reason == "inactive")
                    if change == "cancel"
                    else True
                )


def test_lost_commit_ack_recovers_original_timed_receipt(preparation, monkeypatch):
    fixture = preparation()
    original = Session.commit
    lost = False

    def lose(session):
        nonlocal lost
        original(session)
        if not lost:
            lost = True
            raise SQLAlchemyError(fixture[3]["access_token"])

    with monkeypatch.context() as patch:
        patch.setattr(Session, "commit", lose)
        with pytest.raises(VisitPrepEmailInvalid) as caught:
            _timed(fixture)
    assert caught.value.__cause__ is caught.value.__context__ is None
    assert len(_rows(fixture)) == 1
    assert _timed(fixture).disposition == "replayed" and len(_rows(fixture)) == 1


def test_time_dependent_owner_access_rechecked_after_evidence_read(
    preparation, monkeypatch
):
    fixture = preparation()
    _withdraw_seat(fixture[0].owner, ends_at=datetime.now(UTC) + timedelta(seconds=1))
    from app.services import visit_prep_current_timing as current

    original = current.read_invitation_open_evidence

    def delayed(session, **kwargs):
        result = original(session, **kwargs)
        session.execute(text("SELECT pg_sleep(1.1)"))
        return result

    monkeypatch.setattr(current, "read_invitation_open_evidence", delayed)
    with pytest.raises(VisitPrepEmailInvalid):
        _timed(fixture)
    assert _rows(fixture) == []


def test_historical_missing_open_coverage_remains_unknown(preparation, monkeypatch):
    from app.services import visit_preps

    with monkeypatch.context() as patch:
        patch.setattr(visit_preps, "stage_creation_coverage", lambda *_a, **_k: None)
        fixture = preparation("timing-historical")
    assert _plan(fixture).snapshot.first_party_open_state == "unknown"
    assert _rows(fixture) == []


def test_canonical_appointment_schedule_change_denies_stale_plan(preparation):
    fixture = preparation()
    patient = Patient(
        doctor_id=fixture[0].doctor.id, name="Synthetic timing patient"
    ).save()
    appointment = Appointment(
        doctor_id=fixture[0].doctor.id,
        patient_id=patient.id,
        appointment_date=_plan(fixture).snapshot.scheduled_at,
        external_id=None,
        created_by_system="visit_prep_test",
    ).save()
    reconcile_visit_prep(
        **{
            key: value
            for key, value in _command(fixture).items()
            if key != "expected_credential_generation"
        },
        appointment_id=appointment.id,
    )
    assert _plan(fixture).decisions[0].action == "candidate"
    appointment.appointment_date += timedelta(hours=1)
    appointment.save()
    with pytest.raises(VisitPrepEmailInvalid):
        _timed(fixture)
    assert _rows(fixture) == []


def test_pending_worker_wait_returns_actual_committed_submission(preparation):
    fixture = preparation()
    _timed(fixture)
    claim = claim_email_invitation(**_options(fixture))
    assert isinstance(claim, EmailSubmissionClaim)
    called, release, started = Event(), Event(), Event()

    def transport(_invitation):
        called.set()
        assert release.wait(timeout=5)
        return "accepted"

    def read():
        with global_session() as session:
            session.execute(
                text("SET LOCAL application_name = 'vp_current_timing_worker'")
            )
            started.set()
            return _plan(fixture)

    with ThreadPoolExecutor(max_workers=2) as executor:
        sending = executor.submit(
            dispatch_email_claim, claim, **_options(fixture), transport=transport
        )
        assert called.wait(timeout=5)
        reading = executor.submit(read)
        try:
            assert started.wait(timeout=5)
            with get_session() as observer:
                _observe_waiting_lock(observer, "vp_current_timing_worker")
            assert not reading.done()
        finally:
            release.set()
        assert sending.result(timeout=10) == "accepted"
        plan = reading.result(timeout=10)
    assert plan.email_history[0].state == "accepted"
    assert plan.email_history[0].submitted_at is not None
    assert plan.decisions[0].reason == "already_planned"


def test_final_appointment_clock_after_audit_rolls_back_new_intent(
    preparation, monkeypatch
):
    fixture = preparation()
    scheduled = datetime.now(UTC) + timedelta(seconds=3)
    changed = reschedule_visit_prep(
        **_command(fixture),
        expected_schedule_generation=1,
        scheduled_at=scheduled,
        timezone_name="UTC",
    )
    original = Session.flush

    def delayed(session, *args, **kwargs):
        if any(
            isinstance(row, AuthorityAuditEvent)
            and row.event_type == "visit_prep.email_invitation_queued"
            for row in session.new
        ):
            session.execute(text("SELECT pg_sleep(3.1)"))
        return original(session, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Session, "flush", delayed)
        with pytest.raises(VisitPrepEmailInvalid):
            _timed(
                fixture,
                expected_credential_generation=2,
                access_token=changed.access_token,
            )
    assert _rows(fixture) == []


def test_final_quiet_boundary_recheck_rejects_new_intent(preparation, monkeypatch):
    from app.services import visit_prep_timed_email as timed

    fixture = preparation()
    policy = _policy()
    original = timed._database_now

    def next_quiet(session):
        current = original(session)
        return current.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)

    monkeypatch.setattr(timed, "_database_now", next_quiet)
    with pytest.raises(VisitPrepEmailInvalid):
        _timed(fixture, **policy)
    assert _rows(fixture) == []


def test_history_overflow_denies_instead_of_truncating(preparation, monkeypatch):
    fixture = preparation()
    original = Session.execute

    class Overflow:
        def all(self):
            return [None] * 129

    def overflow(session, statement, *args, **kwargs):
        if "FOR UPDATE OF visit_prep_email_delivery" in str(
            statement.compile(dialect=session.get_bind().dialect)
        ):
            return Overflow()
        return original(session, statement, *args, **kwargs)

    monkeypatch.setattr(Session, "execute", overflow)
    with pytest.raises(VisitPrepEmailInvalid):
        _plan(fixture)
    assert _rows(fixture) == []


@pytest.mark.parametrize("missing_credential", [False, True])
def test_wrong_schedule_cannot_be_accepted_as_credential_history(
    preparation, monkeypatch, missing_credential
):
    fixture = preparation()
    first = _plan(fixture)
    changed = reschedule_visit_prep(
        **_command(fixture),
        expected_schedule_generation=1,
        scheduled_at=first.snapshot.scheduled_at + timedelta(days=1),
        timezone_name="UTC",
    )
    # Test the actual reader against a malformed diagnostic projection without
    # changing immutable production rows or claiming corruption repair.
    with get_session() as session:
        credential = session.exec(
            select(VisitPrepCredentialGeneration).where(
                VisitPrepCredentialGeneration.visit_prep_id == fixture[1].id,
                VisitPrepCredentialGeneration.generation == 2,
            )
        ).one()
        row = VisitPrepEmailDelivery(
            visit_prep_id=fixture[1].id,
            owner_practice_member_id=fixture[0].owner.id,
            question_set_version_id=fixture[1].question_set_version_id,
            schedule_generation_id=credential.schedule_generation_id,
            credential_generation=1,
            authorization_ciphertext="synthetic",
            payload_ciphertext="synthetic",
            requested_at=datetime.now(UTC),
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
        old = session.exec(
            select(VisitPrepCredentialGeneration).where(
                VisitPrepCredentialGeneration.visit_prep_id == fixture[1].id,
                VisitPrepCredentialGeneration.generation == 1,
            )
        ).one()
        projected = (
            row.owner_practice_member_id,
            row.question_set_version_id,
            row.schedule_generation_id,
            row.credential_generation,
            row.template_version,
            row.state,
            row.requested_at,
            row.submitted_at,
            None if missing_credential else old.owner_practice_member_id,
            None if missing_credential else old.schedule_generation_id,
            2,
            row.owner_practice_member_id,
            row.visit_prep_id,
        )
    original = Session.execute

    class Mismatched:
        def all(self):
            return [projected]

    def mismatched(session, statement, *args, **kwargs):
        if "FOR UPDATE OF visit_prep_email_delivery" in str(
            statement.compile(dialect=session.get_bind().dialect)
        ):
            return Mismatched()
        return original(session, statement, *args, **kwargs)

    monkeypatch.setattr(Session, "execute", mismatched)
    with pytest.raises(VisitPrepEmailInvalid):
        _plan(fixture)
    assert changed.access_token is not None and _rows(fixture) == []
