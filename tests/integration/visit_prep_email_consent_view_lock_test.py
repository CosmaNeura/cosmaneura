"""Choice reads and fresh-confirmation capture after actual PostgreSQL waits."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Event

import pytest

from app.services.visit_prep_email_consent_view import PatientEmailConsentView
from app.services.visit_prep_email_contract import VisitPrepEmailInvalid
from app.services.visit_prep_interviews import report_wrong_recipient
from app.services.visit_preps import rotate_visit_prep_credential

from activemodel.session_manager import global_session
from app.models.authority import AuthorityAuditEvent
from app.models.practice import Practice
from sqlalchemy import text
from sqlmodel import Session, select

from tests.integration.visit_prep_contribution_lock_test import _observe_waiting_lock
from tests.services.visit_prep_email_consent_test import _capture, _decision, _rows
from tests.services.visit_prep_email_consent_view_test import (
    _public_capture,
    _read_audits,
    _ready,
    _view,
)
from tests.services.visit_preps_test import _withdraw_seat


@pytest.mark.parametrize("operation", ["read", "decision"])
@pytest.mark.parametrize("change", ["withdrawal", "baa", "rotation", "wrong_recipient"])
def test_waiting_read_and_public_capture_recheck_latest_choice_and_current_authority(
    monkeypatch, operation, change
):
    fixture = _ready(monkeypatch, "consent-view-wait")
    _public_capture(fixture, _decision(fixture))
    intent = _decision(fixture, sequence=1)
    started = Event()

    def run():
        with global_session() as session:
            session.execute(text("SET LOCAL application_name = 'vp_consent_view_wait'"))
            started.set()
            return (
                _view(fixture)
                if operation == "read"
                else _public_capture(fixture, intent)
            )

    with global_session() as parent:
        parent.exec(
            select(Practice).where(Practice.id == fixture.practice.id).with_for_update()
        ).one()
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(run)
            try:
                assert started.wait(timeout=5)
                _observe_waiting_lock(parent, "vp_consent_view_wait")
                if change == "withdrawal":
                    _capture(fixture, _decision(fixture, kind="withdrawn", sequence=1))
                elif change == "baa":
                    fixture.practice.negotiated_baa_on_file = False
                    fixture.practice.save()
                elif change == "wrong_recipient":
                    report_wrong_recipient(access_token=fixture.access_token)
                else:
                    rotate_visit_prep_credential(
                        owner_practice_member_id=fixture.owner.id,
                        owner_doctor_id=fixture.doctor.id,
                        practice_id=fixture.practice.id,
                        visit_prep_id=fixture.prep_id,
                        expected_credential_generation=1,
                    )
            finally:
                parent.commit()
            if operation == "read" and change == "withdrawal":
                current = future.result(timeout=10)
                assert isinstance(current, PatientEmailConsentView)
                assert current.status == "withdrawn" and current.event_sequence == 2
            else:
                with pytest.raises(VisitPrepEmailInvalid) as caught:
                    future.result(timeout=10)
                assert caught.value.__cause__ is caught.value.__context__ is None
    assert len(_rows(fixture)) == (2 if change == "withdrawal" else 1)
    assert len(_read_audits(fixture)) == (
        1 if operation == "read" and change == "withdrawal" else 0
    )


@pytest.mark.parametrize("expiry", ["choice", "seat"])
def test_read_rechecks_database_time_after_real_audit_flush_delay(monkeypatch, expiry):
    fixture = _ready(monkeypatch, "consent-view-elapsed")
    if expiry == "choice":
        _public_capture(
            fixture,
            _decision(fixture, expires_at=datetime.now(UTC) + timedelta(seconds=1)),
        )
    else:
        _public_capture(fixture, _decision(fixture))
        _withdraw_seat(fixture.owner, ends_at=datetime.now(UTC) + timedelta(seconds=1))
    original = Session.flush

    def delay(session, *args, **kwargs):
        if any(
            isinstance(row, AuthorityAuditEvent)
            and row.event_type == "visit_prep.email_consent_read"
            for row in session.new
        ):
            session.execute(text("SELECT pg_sleep(1.1)"))
        return original(session, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Session, "flush", delay)
        if expiry == "choice":
            current = _view(fixture)
            assert current.status == "expired" and current.event_sequence == 1
        else:
            with pytest.raises(VisitPrepEmailInvalid) as caught:
                _view(fixture)
            assert caught.value.__cause__ is caught.value.__context__ is None
    assert len(_read_audits(fixture)) == (1 if expiry == "choice" else 0)
