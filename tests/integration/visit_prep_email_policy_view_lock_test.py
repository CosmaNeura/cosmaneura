"""Current policy display after actual owner-lock waits on owned PostgreSQL."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Event

import pytest

from app.services.visit_prep_email_contract import VisitPrepEmailInvalid
from app.services.visit_prep_email_policy import record_email_policy_decision
from app.services.visit_prep_email_policy_view import get_email_policy_view

from activemodel.session_manager import get_session, global_session
from app.models.authority import AuthorityAuditEvent
from app.models.practice import Practice
from sqlalchemy import text
from sqlmodel import Session, select

from tests.integration.visit_prep_contribution_lock_test import _observe_waiting_lock
from tests.services.visit_prep_email_policy_test import _inputs, _ready
from tests.services.visit_preps_test import _withdraw_seat


def _view(fixture):
    scope, settings = fixture
    return get_email_policy_view(
        owner_practice_member_id=scope.owner.id,
        owner_doctor_id=scope.doctor.id,
        practice_id=scope.practice.id,
        expected_clerk_subject=scope.doctor.clerk_id,
        settings=settings,
    )


@pytest.mark.parametrize("change", ["withdrawal", "baa", "expiry"])
def test_waiting_read_returns_latest_withdrawal_or_expiry_and_denies_baa(change):
    fixture = _ready("policy-view-wait")
    intent = _inputs(fixture)
    if change == "expiry":
        intent["expires_at"] = datetime.now(UTC) + timedelta(seconds=1)
    record_email_policy_decision(**intent)
    started = Event()

    def read():
        with global_session() as session:
            session.execute(text("SET LOCAL application_name = 'vp_policy_read_wait'"))
            started.set()
            return _view(fixture)

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
                _observe_waiting_lock(parent, "vp_policy_read_wait")
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
            if change == "baa":
                with pytest.raises(VisitPrepEmailInvalid):
                    future.result(timeout=10)
            else:
                view = future.result(timeout=10)
                assert view.status == (
                    "withdrawn" if change == "withdrawal" else "expired"
                )
                assert view.event_sequence == (2 if change == "withdrawal" else 1)


def test_read_rechecks_elapsed_seat_authority_after_audit_flush(monkeypatch):
    fixture = _ready("policy-view-final-expiry")
    _withdraw_seat(fixture[0].owner, ends_at=datetime.now(UTC) + timedelta(seconds=1))
    original = Session.flush

    def delay_audit(session, *args, **kwargs):
        if any(isinstance(row, AuthorityAuditEvent) for row in session.new):
            session.execute(text("SELECT pg_sleep(1.1)"))
        return original(session, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Session, "flush", delay_audit)
        with pytest.raises(VisitPrepEmailInvalid) as caught:
            _view(fixture)
    assert caught.value.__cause__ is caught.value.__context__ is None
    with get_session() as session:
        assert not session.exec(
            select(AuthorityAuditEvent).where(
                AuthorityAuditEvent.resource_ref == str(fixture[0].owner.id),
                AuthorityAuditEvent.event_type == "visit_prep.email_policy_read",
            )
        ).all()
