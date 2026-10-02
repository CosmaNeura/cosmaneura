"""Current authorization and withdrawals on independent owned PG connections."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Event

import pytest

from app.services.visit_prep_email_contract import VisitPrepEmailInvalid
from app.services.visit_preps import revoke_visit_prep

from activemodel.session_manager import get_session, global_session
from app.models.practice import Practice
from app.models.visit_prep import VisitPrep
from sqlalchemy import text
from sqlmodel import select

from tests.integration.visit_prep_contribution_lock_test import _observe_waiting_lock
from tests.services.visit_prep_email_authority_test import _ready, _resolve
from tests.services.visit_prep_email_evidence_test import (
    _evidence,
    _store_approval,
    _store_consent,
)


@pytest.mark.parametrize(
    "change", ["approval", "consent", "baa", "revocation", "expiry"]
)
def test_waiting_resolution_rechecks_committed_change_and_actual_elapsed_expiry(change):
    fixture = _ready("authority-real-wait")
    scope, root, _, _ = fixture
    if change == "expiry":
        _, approval, consent = _evidence(
            root, sequence=2, expires=datetime.now(UTC) + timedelta(seconds=1)
        )
        _store_approval(approval)
        _store_consent(consent)
        fixture = (scope, root, consent, fixture[3])
    started = Event()

    def resolve():
        with get_session() as session:
            session.execute(
                text("SET LOCAL application_name = 'vp_email_authority_resolve_wait'")
            )
            started.set()
            result = _resolve(session, fixture)
            session.commit()
            return result.checked_at

    with global_session() as parent:
        model, identifier = (
            (VisitPrep, root.id)
            if change == "consent"
            else (Practice, scope.practice.id)
        )
        parent.exec(select(model).where(model.id == identifier).with_for_update()).one()
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(resolve)
            try:
                assert started.wait(timeout=5)
                _observe_waiting_lock(parent, "vp_email_authority_resolve_wait")
                if change in {"approval", "consent"}:
                    _, approval, consent = _evidence(root, sequence=2, kind="withdrawn")
                    if change == "approval":
                        _store_approval(approval)
                    else:
                        _store_consent(consent)
                elif change == "baa":
                    scope.practice.negotiated_baa_on_file = False
                    scope.practice.save()
                elif change == "revocation":
                    revoke_visit_prep(
                        owner_practice_member_id=scope.owner.id,
                        owner_doctor_id=scope.doctor.id,
                        practice_id=scope.practice.id,
                        visit_prep_id=root.id,
                        expected_credential_generation=1,
                        reason="physician_revoked",
                    )
                else:
                    parent.execute(text("SELECT pg_sleep(1.1)"))
                    assert parent.execute(
                        text("SELECT clock_timestamp() >= :expiry"),
                        {"expiry": fixture[2].expires_at},
                    ).scalar_one()
            finally:
                parent.commit()
            with pytest.raises(VisitPrepEmailInvalid):
                future.result(timeout=10)


@pytest.mark.parametrize("stream", ["approval", "consent"])
def test_current_resolution_holds_withdrawal_fences_until_caller_finishes(stream):
    fixture = _ready("authority-holds-fences")
    root = fixture[1]
    started = Event()

    def withdraw():
        with get_session() as session:
            session.execute(
                text("SET LOCAL application_name = 'vp_email_authority_withdraw_wait'")
            )
            _, approval, consent = _evidence(root, sequence=2, kind="withdrawn")
            started.set()
            row = (
                _store_approval(approval, persist=False)
                if stream == "approval"
                else _store_consent(consent, persist=False)
            )
            identifier = row.id
            session.add(row)
            session.flush()
            session.commit()
            return identifier

    with global_session() as parent:
        resolved = _resolve(parent, fixture)
        assert resolved.consent == fixture[2]
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(withdraw)
            try:
                assert started.wait(timeout=5)
                _observe_waiting_lock(parent, "vp_email_authority_withdraw_wait")
                assert not future.done()
                assert _resolve(parent, fixture).consent == fixture[2]
            finally:
                parent.commit()
            assert future.result(timeout=10) is not None
    with get_session() as session, pytest.raises(VisitPrepEmailInvalid):
        _resolve(session, fixture)
