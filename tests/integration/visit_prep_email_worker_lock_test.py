"""Committed worker races and lost acknowledgments on owned PostgreSQL only."""

from concurrent.futures import ThreadPoolExecutor
from copy import copy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from threading import Barrier, Event

import aiosmtplib
import pytest

import app.services.visit_prep_email_worker as worker
from app.services.visit_prep_email_worker import (
    EmailSubmissionClaim,
    claim_email_invitation,
    dispatch_email_claim,
    maintain_email_secrets,
    run_one_email,
)
from app.services.visit_preps import revoke_visit_prep, rotate_visit_prep_credential

from activemodel.session_manager import SessionManager, global_session
from app.models.practice import Practice
from app.models.visit_prep_delivery import VisitPrepEmailDelivery
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, select

from tests.integration.visit_prep_contribution_lock_test import _observe_waiting_lock
from tests.mailer.visit_prep_email_test import _adapter
from tests.services.visit_prep_email_enqueue_test import _rows
from tests.services.visit_prep_email_evidence_test import (
    _evidence,
    _store_approval,
    _store_consent,
)
from tests.services.visit_prep_email_worker_test import _events, _options, _queued


def test_competing_workers_submit_once_after_independently_visible_claim():
    fixture = _queued("worker-concurrent")
    barrier = Barrier(2)
    calls = []

    def transport(invitation):
        # A separate connection proves the claim committed before the effect,
        # not merely that the worker sees its own uncommitted writes.
        with Session(SessionManager.get_instance().get_engine()) as observer:
            row = observer.exec(
                select(VisitPrepEmailDelivery).where(
                    VisitPrepEmailDelivery.visit_prep_id == fixture[1].id
                )
            ).one()
            assert row.state == "submitting" and row.submitted_at is not None
        calls.append(invitation.operation_id)
        return "accepted"

    def run():
        barrier.wait(timeout=5)
        return run_one_email(**_options(fixture), transport=transport)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(run) for _ in range(2)]
        results = [future.result(timeout=10) for future in futures]
    assert sorted(results) == ["accepted", "idle"]
    assert calls == [fixture[3]["operation_id"]]
    assert _rows(fixture)[0].state == "accepted"
    assert len(_events(fixture)) == 3


@pytest.mark.parametrize("stage", ["claim", "dispatch"])
@pytest.mark.parametrize(
    "change", ["consent", "approval", "baa", "revocation", "rotation", "expiry"]
)
def test_worker_waiting_on_fence_rechecks_actual_committed_change(
    monkeypatch, stage, change
):
    fixture = _queued(
        "worker-wait",
        expiry=datetime.now(UTC) + timedelta(seconds=2) if change == "expiry" else None,
    )
    claim = claim_email_invitation(**_options(fixture)) if stage == "dispatch" else None
    if stage == "dispatch":
        assert isinstance(claim, EmailSubmissionClaim)
    original = worker._lock_rows
    started = Event()
    calls = []

    def marked_lock(session, identifier):
        session.execute(text("SET LOCAL application_name = 'vp_email_worker_wait'"))
        started.set()
        return original(session, identifier)

    monkeypatch.setattr(worker, "_lock_rows", marked_lock)

    def transport(invitation):
        calls.append(invitation)
        return "accepted"

    def run():
        if stage == "dispatch":
            assert isinstance(claim, EmailSubmissionClaim)
            return dispatch_email_claim(claim, **_options(fixture), transport=transport)
        return run_one_email(**_options(fixture), transport=transport)

    scope, root, _, _ = fixture
    with global_session() as parent:
        parent.exec(
            select(Practice).where(Practice.id == scope.practice.id).with_for_update()
        ).one()
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(run)
            try:
                assert started.wait(timeout=5)
                _observe_waiting_lock(parent, "vp_email_worker_wait")
                if change in {"consent", "approval"}:
                    _, approval, consent = _evidence(root, sequence=2, kind="withdrawn")
                    if change == "consent":
                        _store_consent(consent)
                    else:
                        _store_approval(approval)
                elif change == "baa":
                    scope.practice.negotiated_baa_on_file = False
                    scope.practice.save()
                elif change in {"revocation", "rotation"}:
                    arguments = {
                        "owner_practice_member_id": scope.owner.id,
                        "owner_doctor_id": scope.doctor.id,
                        "practice_id": scope.practice.id,
                        "visit_prep_id": root.id,
                        "expected_credential_generation": 1,
                    }
                    if change == "revocation":
                        revoke_visit_prep(**arguments, reason="physician_revoked")
                    else:
                        rotate_visit_prep_credential(**arguments)
                else:
                    parent.execute(text("SELECT pg_sleep(2.1)"))
            finally:
                parent.commit()
            result = future.result(timeout=10)
    assert result == (
        "uncertain" if stage == "dispatch" and change == "expiry" else "suppressed"
    )
    assert calls == [] and _rows(fixture)[0].payload_ciphertext is None


@pytest.mark.parametrize("stream", ["approval", "consent"])
def test_submission_holds_withdrawal_fence_through_provider_and_final_commit(stream):
    fixture = _queued("worker-holds")
    called, release, withdrawing = Event(), Event(), Event()

    def transport(invitation):
        called.set()
        assert release.wait(timeout=5)
        return "accepted"

    def withdraw():
        assert called.wait(timeout=5)
        with Session(SessionManager.get_instance().get_engine()) as session:
            session.execute(
                text("SET LOCAL application_name = 'vp_email_worker_withdraw'")
            )
            _, approval, consent = _evidence(fixture[1], sequence=2, kind="withdrawn")
            row = (
                _store_approval(approval, persist=False)
                if stream == "approval"
                else _store_consent(consent, persist=False)
            )
            withdrawing.set()
            session.add(row)
            session.flush()
            session.commit()

    with ThreadPoolExecutor(max_workers=2) as executor:
        submission = executor.submit(
            run_one_email, **_options(fixture), transport=transport
        )
        withdrawal = executor.submit(withdraw)
        try:
            assert called.wait(timeout=5) and withdrawing.wait(timeout=5)
            with Session(SessionManager.get_instance().get_engine()) as observer:
                _observe_waiting_lock(observer, "vp_email_worker_withdraw")
                assert not withdrawal.done()
        finally:
            release.set()
        assert submission.result(timeout=10) == "accepted"
        withdrawal.result(timeout=10)
    assert _rows(fixture)[0].state == "accepted"
    assert run_one_email(**_options(fixture), transport=transport) == "idle"


def test_lost_claim_commit_ack_never_calls_provider_or_reclaims_send(monkeypatch):
    fixture = _queued(
        "worker-claim-ack", expiry=datetime.now(UTC) + timedelta(seconds=1)
    )
    original = Session.commit
    lost = False
    calls = []

    def commit_then_lose(session):
        nonlocal lost
        original(session)
        if not lost:
            lost = True
            raise SQLAlchemyError("Synthetic lost claim acknowledgment")

    def transport(invitation):
        calls.append(invitation)
        return "accepted"

    monkeypatch.setattr(Session, "commit", commit_then_lose)
    assert (
        run_one_email(**_options(fixture), transport=transport) == "storage_unavailable"
    )
    monkeypatch.setattr(Session, "commit", original)
    assert lost and _rows(fixture)[0].state == "submitting" and calls == []
    assert run_one_email(**_options(fixture), transport=transport) == "idle"
    with Session(SessionManager.get_instance().get_engine()) as observer:
        observer.execute(text("SELECT pg_sleep(1.1)"))
    assert maintain_email_secrets()["uncertain"] == 1
    assert _rows(fixture)[0].payload_ciphertext is None


@pytest.mark.parametrize("lost_after_commit", [True, False])
def test_terminal_storage_failure_never_replays_original_or_copied_claim(
    monkeypatch, lost_after_commit
):
    fixture = _queued(
        "worker-final-ack", expiry=datetime.now(UTC) + timedelta(seconds=1)
    )
    claim = claim_email_invitation(**_options(fixture))
    assert isinstance(claim, EmailSubmissionClaim)
    copied, replaced = copy(claim), replace(claim)
    original = Session.commit
    calls = []

    def fail_commit(session):
        if lost_after_commit:
            original(session)
        raise SQLAlchemyError("Synthetic private finalization failure")

    def transport(invitation):
        calls.append(invitation.operation_id)
        return "accepted"

    monkeypatch.setattr(Session, "commit", fail_commit)
    assert (
        dispatch_email_claim(claim, **_options(fixture), transport=transport)
        == "storage_unavailable"
    )
    monkeypatch.setattr(Session, "commit", original)
    for previous in (claim, copied, replaced):
        assert (
            dispatch_email_claim(previous, **_options(fixture), transport=transport)
            == "discarded"
        )
    assert run_one_email(**_options(fixture), transport=transport) == "idle"
    assert calls == [fixture[3]["operation_id"]]
    assert _rows(fixture)[0].state == (
        "accepted" if lost_after_commit else "submitting"
    )
    assert len(_events(fixture)) == (3 if lost_after_commit else 2)
    if not lost_after_commit:
        with Session(SessionManager.get_instance().get_engine()) as observer:
            observer.execute(text("SELECT pg_sleep(1.1)"))
        assert maintain_email_secrets()["uncertain"] == 1
        assert _rows(fixture)[0].state == "uncertain"
    assert _rows(fixture)[0].payload_ciphertext is None


def test_real_worker_uses_real_core_mailer_with_fake_smtp_only(monkeypatch):
    fixture = _queued("worker-smtp")
    calls = []

    async def fake_smtp(message, **options):
        calls.append((message, options))
        return {}, "Synthetic provider acceptance"

    monkeypatch.setattr(aiosmtplib, "send", fake_smtp)
    with ThreadPoolExecutor(max_workers=1) as executor:
        assert (
            executor.submit(
                run_one_email, **_options(fixture), transport=_adapter()
            ).result(timeout=10)
            == "accepted"
        )
    assert len(calls) == 1
    message, options = calls[0]
    assert str(message["To"]) == "patient@example.com"
    assert (
        str(message["Message-ID"]) == f"<{fixture[3]['operation_id']}@example.invalid>"
    )
    assert fixture[3]["access_token"] in message.get_content()
    assert fixture[3]["access_token"] not in str(message.items())
    assert message.get_content_type() == "text/plain" and not message.is_multipart()
    assert options["use_tls"] is True and options["validate_certs"] is True
    assert (
        _rows(fixture)[0].state == "accepted"
        and _rows(fixture)[0].payload_ciphertext is None
    )
