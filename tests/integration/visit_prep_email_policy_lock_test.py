"""Real owner-lock waits and decision CAS on independent owned PG connections."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier, Event

import pytest

from app.services.visit_prep_email_contract import VisitPrepEmailInvalid
from app.services.visit_prep_email_policy import record_email_policy_decision

from activemodel.session_manager import global_session
from app.models.practice import Practice
from sqlalchemy import text
from sqlmodel import select

from tests.integration.visit_prep_contribution_lock_test import _observe_waiting_lock
from tests.services.visit_prep_email_policy_test import _audits, _inputs, _ready, _rows


@pytest.mark.parametrize("same_operation", [True, False])
def test_competing_confirmations_replay_exact_operation_or_deny_stale_new_key(
    same_operation,
):
    fixture = _ready("policy-competing")
    first = _inputs(fixture)
    inputs = [first, first if same_operation else _inputs(fixture)]
    barrier = Barrier(2)

    def decide(intent):
        barrier.wait(timeout=5)
        try:
            return record_email_policy_decision(**intent).disposition
        except VisitPrepEmailInvalid:
            return "denied"

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(decide, intent) for intent in inputs]
        results = sorted(future.result(timeout=10) for future in futures)
    assert results == (
        ["recorded", "replayed"] if same_operation else ["denied", "recorded"]
    )
    assert len(_rows(fixture[0])) == len(_audits(fixture[0])) == 1


@pytest.mark.parametrize("change", ["withdrawal", "baa", "expiry"])
def test_waiting_confirmation_rechecks_committed_withdrawal_baa_and_elapsed_deadline(
    change,
):
    fixture = _ready("policy-real-wait")
    first = record_email_policy_decision(**_inputs(fixture))
    intent = _inputs(fixture, expected_event_sequence=first.event_sequence)
    if change == "expiry":
        intent["expires_at"] = datetime.now(UTC) + timedelta(seconds=1)
    started = Event()

    def approve():
        with global_session() as session:
            session.execute(
                text("SET LOCAL application_name = 'vp_email_policy_approve_wait'")
            )
            started.set()
            return record_email_policy_decision(**intent)

    with global_session() as parent:
        parent.exec(
            select(Practice)
            .where(Practice.id == fixture[0].practice.id)
            .with_for_update()
        ).one()
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(approve)
            try:
                assert started.wait(timeout=5)
                _observe_waiting_lock(parent, "vp_email_policy_approve_wait")
                if change == "withdrawal":
                    record_email_policy_decision(
                        **_inputs(
                            fixture,
                            expected_event_sequence=1,
                            event_kind="withdrawn",
                            expires_at=None,
                        )
                    )
                elif change == "baa":
                    fixture[0].practice.negotiated_baa_on_file = False
                    fixture[0].practice.save()
                else:
                    parent.execute(text("SELECT pg_sleep(1.1)"))
            finally:
                parent.commit()
            with pytest.raises(VisitPrepEmailInvalid) as caught:
                future.result(timeout=10)
            assert caught.value.__cause__ is None and caught.value.__context__ is None
    rows = _rows(fixture[0])
    assert len(rows) == (2 if change == "withdrawal" else 1)
    assert rows[-1].event_kind == (
        "withdrawn" if change == "withdrawal" else "approved"
    )
    assert len(_audits(fixture[0])) == len(rows)
