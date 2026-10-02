"""Actual enqueue races, withdrawals, expiry and lost commit acknowledgment."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from threading import Barrier, Event
from uuid import uuid4

import pytest

from app.services.visit_prep_email_contract import VisitPrepEmailInvalid
from app.services.visit_prep_email_enqueue import (
    enqueue_email_invitation,
    enqueue_locked_email_invitation,
)
from app.services.visit_preps import revoke_visit_prep, rotate_visit_prep_credential

from activemodel.session_manager import get_session, global_session
from app.models.practice import Practice
from app.models.visit_prep import VisitPrep
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, select

from tests.integration.visit_prep_contribution_lock_test import _observe_waiting_lock
from tests.services.visit_prep_email_enqueue_test import _audits, _ready, _rows
from tests.services.visit_prep_email_evidence_test import (
    _evidence,
    _store_approval,
    _store_consent,
)


@pytest.mark.parametrize("same_key", [True, False])
def test_concurrent_enqueues_leave_one_intent_and_one_audit(same_key):
    fixture = _ready("enqueue-concurrent")
    barrier = Barrier(2)

    def request(index):
        arguments = fixture[3] | {
            "operation_id": fixture[3]["operation_id"]
            if same_key or index == 0
            else str(uuid4())
        }
        barrier.wait(timeout=5)
        try:
            return enqueue_email_invitation(**arguments)
        except VisitPrepEmailInvalid:
            return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(request, index) for index in range(2)]
        results = [future.result(timeout=10) for future in futures]
    assert len(_rows(fixture)) == 1
    if same_key:
        assert all(result is not None for result in results)
        assert {result.disposition for result in results if result is not None} == {
            "queued",
            "replayed",
        }
        assert (
            len({result.delivery_id for result in results if result is not None}) == 1
        )
        assert len(_audits(fixture)) == 1
    else:
        assert results.count(None) == 1


@pytest.mark.parametrize(
    "change", ["approval", "consent", "baa", "revocation", "rotation", "expiry"]
)
def test_enqueue_waiting_on_authority_rechecks_committed_change(change):
    fixture = _ready("enqueue-wait")
    scope, root, consent, arguments = fixture
    if change == "expiry":
        _, approval, consent = _evidence(
            root, sequence=2, expires=datetime.now(UTC) + timedelta(seconds=1)
        )
        _store_approval(approval)
        _store_consent(consent)

        def invented(_session, evidence, *, checked_at):
            return evidence == consent

        arguments = arguments | {"source_verifier": invented}
    started = Event()

    def request():
        with global_session() as session:
            session.execute(
                text("SET LOCAL application_name = 'vp_email_enqueue_wait'")
            )
            started.set()
            return enqueue_email_invitation(**arguments)

    with global_session() as parent:
        model, identifier = (
            (VisitPrep, root.id)
            if change == "consent"
            else (Practice, scope.practice.id)
        )
        parent.exec(select(model).where(model.id == identifier).with_for_update()).one()
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(request)
            try:
                assert started.wait(timeout=5)
                _observe_waiting_lock(parent, "vp_email_enqueue_wait")
                if change in {"approval", "consent"}:
                    _, approval, consent = _evidence(root, sequence=2, kind="withdrawn")
                    _store_consent(consent) if change == "consent" else _store_approval(
                        approval
                    )
                elif change == "baa":
                    scope.practice.negotiated_baa_on_file = False
                    scope.practice.save()
                elif change in {"revocation", "rotation"}:
                    if change == "revocation":
                        revoke_visit_prep(
                            owner_practice_member_id=scope.owner.id,
                            owner_doctor_id=scope.doctor.id,
                            practice_id=scope.practice.id,
                            visit_prep_id=root.id,
                            expected_credential_generation=1,
                            reason="physician_revoked",
                        )
                    else:
                        rotate_visit_prep_credential(
                            owner_practice_member_id=scope.owner.id,
                            owner_doctor_id=scope.doctor.id,
                            practice_id=scope.practice.id,
                            visit_prep_id=root.id,
                            expected_credential_generation=1,
                        )
                else:
                    parent.execute(text("SELECT pg_sleep(1.1)"))
                    assert parent.execute(
                        text("SELECT clock_timestamp() >= :expiry"),
                        {"expiry": consent.expires_at},
                    ).scalar_one()
            finally:
                parent.commit()
            with pytest.raises(VisitPrepEmailInvalid):
                future.result(timeout=10)
    assert _rows(fixture) == [] and _audits(fixture) == []


@pytest.mark.parametrize("stream", ["approval", "consent"])
def test_handoff_holds_withdrawal_fences_until_its_atomic_commit(stream):
    fixture = _ready("enqueue-holds")
    started = Event()

    def withdraw():
        with get_session() as session:
            session.execute(
                text("SET LOCAL application_name = 'vp_email_enqueue_withdraw_wait'")
            )
            _, approval, consent = _evidence(fixture[1], sequence=2, kind="withdrawn")
            row = (
                _store_approval(approval, persist=False)
                if stream == "approval"
                else _store_consent(consent, persist=False)
            )
            started.set()
            session.add(row)
            session.flush()
            session.commit()
            return row.id

    with global_session() as parent:
        receipt = enqueue_locked_email_invitation(parent, **fixture[3])
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(withdraw)
            try:
                assert started.wait(timeout=5)
                _observe_waiting_lock(parent, "vp_email_enqueue_withdraw_wait")
                assert not future.done()
                assert receipt.disposition == "queued"
            finally:
                parent.commit()
            assert future.result(timeout=10) is not None
    assert len(_rows(fixture)) == len(_audits(fixture)) == 1
    # The existing row/receipt is not permission to send under the new stream.
    from app.services.visit_prep_email_authority import resolve_locked_email_authority

    authority_arguments = {
        key: value
        for key, value in fixture[3].items()
        if key
        not in {
            "expected_credential_generation",
            "access_token",
            "operation_id",
            "patient_origin",
        }
    }
    with get_session() as session, pytest.raises(VisitPrepEmailInvalid):
        resolve_locked_email_authority(session, **authority_arguments)


def test_lost_commit_ack_recovers_exact_intent_without_reenqueuing(monkeypatch):
    fixture = _ready("enqueue-lost-ack")
    original_commit = Session.commit
    lost = False

    def commit_then_lose_ack(session):
        nonlocal lost
        original_commit(session)
        if not lost:
            lost = True
            raise SQLAlchemyError("synthetic lost acknowledgment")

    monkeypatch.setattr(Session, "commit", commit_then_lose_ack)
    with pytest.raises(VisitPrepEmailInvalid) as caught:
        enqueue_email_invitation(**fixture[3])
    assert caught.value.__context__ is None and caught.value.__cause__ is None
    monkeypatch.setattr(Session, "commit", original_commit)
    assert lost and len(_rows(fixture)) == len(_audits(fixture)) == 1
    receipt = enqueue_email_invitation(**fixture[3])
    assert receipt.disposition == "replayed"
    assert len(_rows(fixture)) == len(_audits(fixture)) == 1


def test_a_rotated_credential_requires_its_actual_new_bearer_and_own_operation():
    fixture = _ready("enqueue-new-generation")
    scope, root, _, arguments = fixture
    first = enqueue_email_invitation(**arguments)
    rotated = rotate_visit_prep_credential(
        owner_practice_member_id=scope.owner.id,
        owner_doctor_id=scope.doctor.id,
        practice_id=scope.practice.id,
        visit_prep_id=root.id,
        expected_credential_generation=1,
    )
    assert rotated.access_token is not None
    changed = arguments | {
        "expected_credential_generation": 2,
        "access_token": rotated.access_token,
        "operation_id": str(uuid4()),
    }
    second = enqueue_email_invitation(**changed)
    assert first.delivery_id != second.delivery_id
    assert {row.credential_generation for row in _rows(fixture)} == {1, 2}


def test_expired_secret_receipt_does_not_restore_its_delivery_window():
    fixture = _ready(expiry=datetime.now(UTC) + timedelta(seconds=2))
    first = enqueue_email_invitation(**fixture[3])
    with get_session() as session:
        session.execute(text("SELECT pg_sleep(2.1)"))
    replay = enqueue_email_invitation(
        **(
            fixture[3]
            | {
                "source_verifier": None,
                "settings": replace(fixture[3]["settings"], email_send_enabled=False),
            }
        )
    )
    assert replay.disposition == "replayed" and replay.expires_at == first.expires_at
    assert replay.expires_at < datetime.now(UTC)
    assert len(_rows(fixture)) == 1
