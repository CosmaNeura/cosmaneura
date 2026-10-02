"""Durable physician choices and actual SMS worker; no qualified live sender."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from threading import Barrier, Event
from uuid import uuid4

import pytest

from app.configuration import visit_prep_sms
from app.services import visit_prep_sms_outbox as outbox
from app.services import visit_prep_sms_policy as policy
from app.services import visit_prep_sms_worker as worker
from app.services.visit_prep_sms_contract import (
    POLICY_FINGERPRINT,
    VisitPrepSMSUnavailable,
)
from app.services.visit_prep_sms_suppression import sms_sender_digest
from app.utils.encryption import decrypt_value

from activemodel.session_manager import get_engine
from app.models.authority import AuthorityAuditEvent
from app.models.practice import Practice
from app.models.visit_prep_sms_approval import VisitPrepSMSApprovalEvent
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlmodel import Session, col, select

from tests.integration.visit_prep_contribution_lock_test import _observe_waiting_lock
from tests.integration.visit_prep_sms_outbox_test import (
    _finish_queue_only_fixtures,  # noqa: F401 -- keep only owned queue fixtures terminal
    _row,
)
from tests.integration.visit_prep_sms_outbox_test import (
    _ready as _sms_ready,
)
from tests.integration.visit_prep_sms_worker_test import _worker
from tests.services.visit_prep_email_policy_test import _ready as _owner_ready


def _scope(fixture, settings):
    return {
        "owner_practice_member_id": fixture.owner.id,
        "owner_doctor_id": fixture.doctor.id,
        "practice_id": fixture.practice.id,
        "expected_clerk_subject": fixture.doctor.clerk_id,
        "settings": settings,
    }


def _intent(fixture, settings, routing, **changes):
    return (
        _scope(fixture, settings)
        | {
            "operation_id": str(uuid4()),
            "expected_event_sequence": 0,
            "event_kind": "approved",
            "sender_digest": sms_sender_digest(routing),
            "policy_fingerprint": POLICY_FINGERPRINT,
            "expires_at": datetime.now(UTC) + timedelta(hours=23),
        }
        | changes
    )


def _policy_ready(monkeypatch):
    fixture, settings = _owner_ready("sms-policy")
    routing = visit_prep_sms.VisitPrepSMSRouting(
        "synthetic", str(uuid4()), "+12025550100"
    )
    monkeypatch.setattr(visit_prep_sms, "ROUTING", routing)
    return fixture, settings, routing


def _rows(fixture):
    with Session(get_engine()) as session:
        return session.exec(
            select(VisitPrepSMSApprovalEvent)
            .where(
                VisitPrepSMSApprovalEvent.owner_practice_member_id == fixture.owner.id
            )
            .order_by(col(VisitPrepSMSApprovalEvent.event_sequence))
        ).all()


def _audits(fixture):
    refs = [str(row.id) for row in _rows(fixture)]
    with Session(get_engine()) as session:
        return session.exec(
            select(AuthorityAuditEvent).where(
                AuthorityAuditEvent.resource_type == "visit_prep_sms_approval",
                col(AuthorityAuditEvent.resource_ref).in_(refs),
            )
        ).all()


def _deny(intent):
    with pytest.raises(VisitPrepSMSUnavailable) as caught:
        policy.record_sms_policy_decision(**intent)
    assert caught.value.__cause__ is None and caught.value.__context__ is None


def _approval(fixture, settings):
    with Session(get_engine()) as session:
        return policy.read_locked_sms_policy_approval(
            session, **_scope(fixture, settings)
        )


def _with_durable_policy(monkeypatch):
    fixture, options, routing = _sms_ready(monkeypatch)
    intent = _intent(fixture, options["settings"], routing)
    policy.record_sms_policy_decision(**intent)
    approval = _approval(fixture, options["settings"])

    def synthetic_sender(session, *, sender_digest, checked_at):
        # Invented qualification only: proves composition, NOT a real vendor approval.
        return (
            sender_digest == sms_sender_digest(routing)
            and checked_at >= approval.effective_at
        )

    options = options | {
        "approval": approval,
        "approval_verifier": policy.CoreSMSApprovalVerifier(
            options["settings"], synthetic_sender
        ),
    }
    return fixture, options, intent


def test_capture_and_exact_retry_are_private_atomic_and_do_not_grant_patient_consent(
    monkeypatch,
):
    fixture, settings, routing = _policy_ready(monkeypatch)
    intent = _intent(fixture, settings, routing)
    receipt = policy.record_sms_policy_decision(**intent)
    assert receipt.disposition == "recorded"
    assert receipt.event_sequence == receipt.current_event_sequence == 1
    row = _rows(fixture)[0]
    plaintext = decrypt_value(row.evidence_ciphertext)
    assert (
        fixture.doctor.clerk_id not in plaintext
        and routing.sender_number not in plaintext
    )
    assert all(
        key not in row.model_dump()
        for key in (
            "evidence_ciphertext",
            "subject_digest",
            "sender_digest",
            "payload_hmac",
        )
    )
    with Session(get_engine()) as session:
        policy._verify(session, row)
        assert (
            session.execute(
                text(
                    "SELECT count(*) FROM visit_prep_sms_consent_event WHERE owner_practice_member_id=:owner"
                ),
                {"owner": fixture.owner.id.uuid},
            ).scalar_one()
            == 0
        )
        assert (
            session.execute(
                text(
                    "SELECT count(*) FROM visit_prep_sms_delivery WHERE owner_practice_member_id=:owner"
                ),
                {"owner": fixture.owner.id.uuid},
            ).scalar_one()
            == 0
        )
    audit = _audits(fixture)[0]
    assert audit.request_digest == row.payload_hmac
    assert (
        audit.actor_id is None and audit.person_id is None and audit.tenant_id is None
    )
    assert settings.sms_send_enabled is False and settings.public_api_enabled is False
    replay = policy.record_sms_policy_decision(**intent)
    assert replay == replace(receipt, disposition="replayed")
    assert _rows(fixture)[0].evidence_ciphertext == row.evidence_ciphertext
    assert len(_rows(fixture)) == len(_audits(fixture)) == 1


def test_withdrawal_and_historical_retry_require_a_fresh_approval(monkeypatch):
    fixture, settings, routing = _policy_ready(monkeypatch)
    intent = _intent(fixture, settings, routing)
    first = policy.record_sms_policy_decision(**intent)
    withdrawn = policy.record_sms_policy_decision(
        **(
            intent
            | {
                "operation_id": str(uuid4()),
                "expected_event_sequence": 1,
                "event_kind": "withdrawn",
                "expires_at": None,
            }
        )
    )
    assert withdrawn.event_sequence == 2 and withdrawn.expires_at is None
    replay = policy.record_sms_policy_decision(**intent)
    assert replay.event_id == first.event_id and replay.current_event_sequence == 2
    with pytest.raises(VisitPrepSMSUnavailable):
        _approval(fixture, settings)
    _deny(intent | {"operation_id": str(uuid4())})
    renewed = policy.record_sms_policy_decision(
        **(
            intent
            | {
                "operation_id": str(uuid4()),
                "expected_event_sequence": 2,
            }
        )
    )
    assert renewed.current_event_sequence == 3
    assert _approval(fixture, settings).effective_at == renewed.recorded_at


@pytest.mark.parametrize("change", ["sender", "policy", "removed"])
def test_sender_or_policy_changes_invalidate_approval_but_not_withdrawal(
    monkeypatch, change
):
    fixture, settings, routing = _policy_ready(monkeypatch)
    intent = _intent(fixture, settings, routing)
    policy.record_sms_policy_decision(**intent)
    if change == "policy":
        monkeypatch.setattr(policy, "POLICY_FINGERPRINT", "a" * 64)
    else:
        monkeypatch.setattr(
            visit_prep_sms,
            "ROUTING",
            None
            if change == "removed"
            else replace(routing, sender_number="+12025550101"),
        )
    with pytest.raises(VisitPrepSMSUnavailable):
        _approval(fixture, settings)
    _deny(intent | {"operation_id": str(uuid4()), "expected_event_sequence": 1})
    withdrawal = policy.record_sms_policy_decision(
        **(
            intent
            | {
                "operation_id": str(uuid4()),
                "expected_event_sequence": 1,
                "event_kind": "withdrawn",
                "expires_at": None,
            }
        )
    )
    assert withdrawal.event_kind == "withdrawn"
    assert policy.record_sms_policy_decision(**intent).current_event_sequence == 2


@pytest.mark.parametrize(
    "changes",
    [
        {"operation_id": "not-a-uuid"},
        {"expected_event_sequence": True},
        {"expected_event_sequence": 1},
        {"policy_fingerprint": "f" * 64},
        {"sender_digest": "f" * 64},
        {"expected_clerk_subject": "other-synthetic-actor"},
        {"view_as_active": True},
        {"expires_at": datetime.now(UTC) - timedelta(seconds=1)},
        {"expires_at": datetime.now()},
        {"expires_at": None},
    ],
)
def test_invalid_or_unowned_decisions_leave_no_record_or_audit(monkeypatch, changes):
    fixture, settings, routing = _policy_ready(monkeypatch)
    _deny(_intent(fixture, settings, routing) | changes)
    assert _rows(fixture) == [] and _audits(fixture) == []


@pytest.mark.parametrize("change", ["payload", "audit", "late_owner"])
def test_atomic_failures_rollback_and_hide_private_details(monkeypatch, change):
    fixture, settings, routing = _policy_ready(monkeypatch)
    intent = _intent(fixture, settings, routing)
    if change == "payload":

        def encrypt(value):
            raise ValueError("Synthetic private encryption failure")

        monkeypatch.setattr(policy, "encrypt_value", encrypt)
    elif change == "audit":
        original = Session.add

        def add(session, instance, *args, **kwargs):
            if isinstance(instance, AuthorityAuditEvent):
                raise ValueError("Synthetic private audit failure")
            return original(session, instance, *args, **kwargs)

        monkeypatch.setattr(Session, "add", add)
    else:
        original = policy._authorize_owner
        calls = []

        def authorize(*args, **kwargs):
            calls.append(1)
            if len(calls) == 3:
                raise ValueError("Synthetic changed owner authority")
            return original(*args, **kwargs)

        monkeypatch.setattr(policy, "_authorize_owner", authorize)
    _deny(intent)
    assert _rows(fixture) == [] and _audits(fixture) == []


def test_divergent_operation_reuse_and_raw_sql_mutation_are_denied(monkeypatch):
    fixture, settings, routing = _policy_ready(monkeypatch)
    intent = _intent(fixture, settings, routing)
    policy.record_sms_policy_decision(**intent)
    for changes in (
        {"expected_event_sequence": 1},
        {"expires_at": intent["expires_at"] + timedelta(seconds=1)},
        {"event_kind": "withdrawn", "expires_at": None},
        {"sender_digest": "a" * 64},
    ):
        _deny(intent | changes)
    for statement in (
        "UPDATE visit_prep_sms_approval_event SET policy_fingerprint=:value WHERE operation_id=:op",
        "DELETE FROM visit_prep_sms_approval_event WHERE operation_id=:op",
        "TRUNCATE visit_prep_sms_approval_event",
    ):
        with Session(get_engine()) as session, pytest.raises(DBAPIError) as caught:
            session.execute(
                text(statement), {"op": intent["operation_id"], "value": "a" * 64}
            )
            session.commit()
        assert caught.value.orig.sqlstate == "55000"
    assert len(_rows(fixture)) == len(_audits(fixture)) == 1


@pytest.mark.parametrize("same_operation", [True, False])
def test_two_independent_writers_replay_exact_operation_or_deny_stale_key(
    monkeypatch, same_operation
):
    fixture, settings, routing = _policy_ready(monkeypatch)
    intent = _intent(fixture, settings, routing)
    barrier = Barrier(2)

    def decide(inputs):
        barrier.wait(timeout=5)
        try:
            return policy.record_sms_policy_decision(**inputs).disposition
        except VisitPrepSMSUnavailable:
            return "denied"

    second = intent if same_operation else intent | {"operation_id": str(uuid4())}
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(decide, inputs) for inputs in (intent, second)]
        result = sorted(future.result(timeout=10) for future in futures)
    assert result == (
        ["recorded", "replayed"] if same_operation else ["denied", "recorded"]
    )
    assert len(_rows(fixture)) == len(_audits(fixture)) == 1


def test_actual_queue_and_single_attempt_worker_consume_the_durable_choice(monkeypatch):
    _, options, _ = _with_durable_policy(monkeypatch)
    receipt = outbox.enqueue_sms(**options)
    seen = []

    def synthetic_transport(message):
        seen.append(message)
        return "accepted"

    assert (
        worker.run_one_sms(**_worker(options, transport=synthetic_transport))
        == "accepted"
    )
    assert (
        worker.run_one_sms(**_worker(options, transport=synthetic_transport)) == "idle"
    )
    assert len(seen) == 1 and _row(receipt).payload_ciphertext is None


@pytest.mark.parametrize(
    "change", ["no_sender_source", "sender_denied", "no_patient_source"]
)
def test_durable_physician_approval_never_substitutes_for_independent_qualification(
    monkeypatch, change
):
    _, options, _ = _with_durable_policy(monkeypatch)
    if change == "no_patient_source":
        options["consent_verifier"] = None
    else:
        source = None if change == "no_sender_source" else lambda *a, **kw: False
        options["approval_verifier"] = policy.CoreSMSApprovalVerifier(
            options["settings"], source
        )
    with pytest.raises(VisitPrepSMSUnavailable):
        outbox.enqueue_sms(**options)


def test_actual_withdrawal_between_claim_and_dispatch_blocks_the_transport(monkeypatch):
    _, options, intent = _with_durable_policy(monkeypatch)
    receipt = outbox.enqueue_sms(**options)
    claim = worker.claim_sms_invitation(**_worker(options))
    assert isinstance(claim, worker.SMSSubmissionClaim)
    policy.record_sms_policy_decision(
        **(
            intent
            | {
                "operation_id": str(uuid4()),
                "expected_event_sequence": 1,
                "event_kind": "withdrawn",
                "expires_at": None,
            }
        )
    )
    seen = []
    assert (
        worker.dispatch_sms_claim(
            claim, **_worker(options, transport=lambda m: seen.append(m))
        )
        == "suppressed"
    )
    assert seen == [] and _row(receipt).payload_ciphertext is None


def test_source_verifier_cannot_release_the_current_transaction(monkeypatch):
    fixture, options, _ = _with_durable_policy(monkeypatch)

    def releasing_source(session, **kwargs):
        session.commit()
        return True

    verifier = policy.CoreSMSApprovalVerifier(options["settings"], releasing_source)
    with Session(get_engine()) as session:
        session.execute(text("SELECT 1"))
        assert (
            verifier(session, options["approval"], checked_at=datetime.now(UTC))
            is False
        )
    assert len(_rows(fixture)) == 1


@pytest.mark.parametrize("change", ["baa", "expiry"])
def test_approval_waiting_on_real_owner_lock_rechecks_committed_authority_and_time(
    monkeypatch, change
):
    fixture, settings, routing = _policy_ready(monkeypatch)
    intent = _intent(fixture, settings, routing)
    if change == "expiry":
        intent["expires_at"] = datetime.now(UTC) + timedelta(seconds=1)
    started = Event()

    def named_session(*args, **kwargs):
        session = Session(*args, **kwargs)
        session.execute(text("SET LOCAL application_name = 'vp_sms_policy_owner_wait'"))
        started.set()
        return session

    monkeypatch.setattr(policy, "Session", named_session)
    with Session(get_engine()) as parent:
        parent.exec(
            select(Practice).where(Practice.id == fixture.practice.id).with_for_update()
        ).one()
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(policy.record_sms_policy_decision, **intent)
            try:
                assert started.wait(timeout=5)
                _observe_waiting_lock(parent, "vp_sms_policy_owner_wait")
                if change == "baa":
                    parent.execute(
                        text(
                            "UPDATE practice SET negotiated_baa_on_file=false WHERE id=:id"
                        ),
                        {"id": fixture.practice.id.uuid},
                    )
                else:
                    parent.execute(text("SELECT pg_sleep(1.1)"))
            finally:
                parent.commit()
            with pytest.raises(VisitPrepSMSUnavailable):
                future.result(timeout=10)
    assert _rows(fixture) == [] and _audits(fixture) == []


def test_retained_approval_retry_still_requires_current_owner_authority(monkeypatch):
    fixture, settings, routing = _policy_ready(monkeypatch)
    intent = _intent(fixture, settings, routing)
    policy.record_sms_policy_decision(**intent)
    with Session(get_engine()) as session:
        session.execute(
            text("UPDATE practice SET negotiated_baa_on_file=false WHERE id=:id"),
            {"id": fixture.practice.id.uuid},
        )
        session.commit()
    _deny(intent)
    with pytest.raises(VisitPrepSMSUnavailable):
        _approval(fixture, settings)
    assert len(_rows(fixture)) == len(_audits(fixture)) == 1


@pytest.mark.parametrize("plaintext", ['{"purpose":1,"purpose":2}', "{}", "not-json"])
def test_broken_private_evidence_cannot_be_normalized_or_replayed(
    monkeypatch, plaintext
):
    fixture, settings, routing = _policy_ready(monkeypatch)
    intent = _intent(fixture, settings, routing)
    policy.record_sms_policy_decision(**intent)
    monkeypatch.setattr(policy, "decrypt_value", lambda value: plaintext)
    with pytest.raises(VisitPrepSMSUnavailable):
        _approval(fixture, settings)
    _deny(intent)
    assert len(_rows(fixture)) == len(_audits(fixture)) == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"event_sequence": 3},
        {"recorded_at": datetime.now(UTC) + timedelta(days=1)},
        {"expires_at": None},
        {"event_kind": "withdrawn"},
    ],
)
def test_raw_insert_cannot_skip_sequence_or_bypass_time_shape(monkeypatch, changes):
    fixture, settings, routing = _policy_ready(monkeypatch)
    policy.record_sms_policy_decision(**_intent(fixture, settings, routing))
    original = _rows(fixture)[0]
    values = {
        column.name: getattr(original, column.name)
        for column in VisitPrepSMSApprovalEvent.__table__.columns
    }
    values |= {
        "id": policy.TypeID("visit_prep_sms_approval"),
        "operation_id": uuid4(),
        "event_sequence": 2,
    }
    values |= changes
    with Session(get_engine()) as session, pytest.raises(DBAPIError):
        session.execute(VisitPrepSMSApprovalEvent.__table__.insert().values(**values))
        session.commit()
    assert len(_rows(fixture)) == len(_audits(fixture)) == 1
