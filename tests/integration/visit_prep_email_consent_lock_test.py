"""Actual concurrent consent choices using independent owned PG connections."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier, Event

import pytest

from app.services.visit_prep_email_contract import VisitPrepEmailInvalid
from app.services.visit_prep_interviews import report_wrong_recipient
from app.services.visit_preps import rotate_visit_prep_credential

from activemodel.session_manager import global_session
from app.models.practice import Practice
from sqlalchemy import text
from sqlmodel import select

from tests.integration.visit_prep_contribution_lock_test import _observe_waiting_lock
from tests.services.visit_prep_email_consent_test import (
    _audits,
    _capture,
    _decision,
    _ready,
    _rows,
)


@pytest.mark.parametrize("same_operation", [True, False])
def test_competing_choices_replay_one_exact_operation_or_deny_stale_new_key(
    monkeypatch, same_operation
):
    fixture = _ready(monkeypatch, "consent-competing")
    first = _decision(fixture)
    intents = [first, first if same_operation else _decision(fixture)]
    barrier = Barrier(2)

    def choose(intent):
        barrier.wait(timeout=5)
        try:
            return _capture(fixture, intent).disposition
        except VisitPrepEmailInvalid:
            return "denied"

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(choose, intent) for intent in intents]
        results = sorted(future.result(timeout=10) for future in futures)
    assert results == (
        ["recorded", "replayed"] if same_operation else ["denied", "recorded"]
    )
    assert len(_rows(fixture)) == len(_audits(fixture)) == 1


@pytest.mark.parametrize(
    "change", ["withdrawal", "baa", "expiry", "wrong_recipient", "rotation"]
)
def test_waiting_choice_rechecks_current_withdrawal_authority_access_and_deadline(
    monkeypatch, change
):
    fixture = _ready(monkeypatch, "consent-real-wait")
    _capture(fixture, _decision(fixture))
    intent = _decision(fixture, sequence=1)
    if change == "expiry":
        intent = _decision(
            fixture, sequence=1, expires_at=datetime.now(UTC) + timedelta(seconds=1)
        )
    started = Event()

    def choose():
        with global_session() as session:
            session.execute(
                text("SET LOCAL application_name = 'vp_email_consent_choice_wait'")
            )
            started.set()
            return _capture(fixture, intent)

    with global_session() as parent:
        parent.exec(
            select(Practice).where(Practice.id == fixture.practice.id).with_for_update()
        ).one()
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(choose)
            try:
                assert started.wait(timeout=5)
                _observe_waiting_lock(parent, "vp_email_consent_choice_wait")
                if change == "withdrawal":
                    _capture(fixture, _decision(fixture, kind="withdrawn", sequence=1))
                elif change == "baa":
                    fixture.practice.negotiated_baa_on_file = False
                    fixture.practice.save()
                elif change == "wrong_recipient":
                    report_wrong_recipient(access_token=fixture.access_token)
                elif change == "rotation":
                    rotate_visit_prep_credential(
                        owner_practice_member_id=fixture.owner.id,
                        owner_doctor_id=fixture.doctor.id,
                        practice_id=fixture.practice.id,
                        visit_prep_id=fixture.prep_id,
                        expected_credential_generation=1,
                    )
                else:
                    parent.execute(text("SELECT pg_sleep(1.1)"))
            finally:
                parent.commit()
            with pytest.raises(VisitPrepEmailInvalid) as caught:
                future.result(timeout=10)
            assert caught.value.__cause__ is None and caught.value.__context__ is None
    rows = _rows(fixture)
    assert len(rows) == (2 if change == "withdrawal" else 1)
    assert rows[-1].event_kind == ("withdrawn" if change == "withdrawal" else "granted")
    assert len(_audits(fixture)) == len(rows)
