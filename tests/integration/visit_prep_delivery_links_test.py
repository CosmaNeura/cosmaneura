"""Real bounded links, remembered patient return and atomic synthetic SMS handoff."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from threading import Barrier
from uuid import uuid4

import pytest

from app.services import visit_prep_delivery_links as links
from app.services import visit_prep_interviews as interviews
from app.services import visit_prep_sms_outbox as sms
from app.services import visit_prep_sms_worker as worker
from app.services.visit_prep_command_contract import access_token_hash
from app.services.visit_prep_linked_sms import enqueue_linked_sms
from app.services.visit_prep_patient_continuation import COOKIE_NAME, open_confirmation
from app.services.visit_prep_sms_contract import VisitPrepSMSUnavailable
from app.services.visit_preps import reschedule_visit_prep, rotate_visit_prep_credential
from app.utils.encryption import decrypt_value

from activemodel.session_manager import get_engine
from app.models.authority import AuthorityAuditEvent
from app.models.practice import Practice
from app.models.visit_prep_delivery_link import VisitPrepDeliveryLink
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlmodel import Session, select

from tests.integration.visit_prep_patient_continuation_http_test import (
    CONTINUED,
    PREFIX,
    _client,
)
from tests.integration.visit_prep_sms_outbox_test import (
    _finish_queue_only_fixtures,  # noqa: F401 -- finalize only this test's owned queues
    _row,
)
from tests.integration.visit_prep_sms_outbox_test import (
    _ready as _sms_ready,
)
from tests.integration.visit_prep_sms_worker_test import _worker
from tests.services.visit_prep_interviews_test import _fixture
from tests.services.visit_preps_test import _lineage


def _intent(fixture, *, expires_at=None):
    return links.DeliveryLinkIntent(
        fixture.prep_id,
        _lineage(fixture.prep_id)[2][-1].id,
        str(uuid4()),
        expires_at or datetime.now(UTC) + timedelta(hours=1),
    )


def _arguments(fixture, intent):
    return dict(
        intent=intent,
        settings=interviews.visit_prep_access.VISIT_PREP_SETTINGS,
        owner_practice_member_id=fixture.owner.id,
        owner_doctor_id=fixture.doctor.id,
        practice_id=fixture.practice.id,
        expected_clerk_subject=fixture.doctor.clerk_id,
        # Only invented qualification; a real booking contact grants nothing.
        source_verifier=lambda session, candidate, **kw: candidate == intent,
    )


def _rows(fixture):
    with Session(get_engine()) as session:
        return session.exec(
            select(VisitPrepDeliveryLink).where(
                VisitPrepDeliveryLink.visit_prep_id == fixture.prep_id
            )
        ).all()


def _deny(arguments):
    with pytest.raises(links.DeliveryLinkUnavailable) as caught:
        links.issue_delivery_link(**arguments)
    assert caught.value.__cause__ is None and caught.value.__context__ is None


def _linked(options, intent):
    return dict(
        intent=intent,
        **{
            key: options[key]
            for key in (
                "step",
                "patient_origin",
                "approval",
                "settings",
                "approval_verifier",
                "consent_verifier",
            )
        },
    )


def test_bearer_returned_once_without_rotating_canonical_or_storing_plaintext(
    monkeypatch,
):
    fixture = _fixture(monkeypatch, "alias-once")
    original = _lineage(fixture.prep_id)[2][-1]
    intent = _intent(fixture)
    arguments = _arguments(fixture, intent)
    receipt = links.issue_delivery_link(**arguments)
    assert (
        receipt.disposition == "issued" and receipt.access_token != fixture.access_token
    )
    assert links.issue_delivery_link(**arguments) == replace(
        receipt, disposition="replayed", access_token=None
    )
    assert len(_rows(fixture)) == 1
    row = _rows(fixture)[0]
    plaintext = decrypt_value(row.evidence_ciphertext)
    assert (
        receipt.access_token not in plaintext and fixture.access_token not in plaintext
    )
    assert (
        fixture.doctor.clerk_id not in plaintext
        and "patient@example.com" not in plaintext
    )
    assert receipt.access_token not in repr(receipt) + repr(row)
    assert {"token_hash", "payload_hmac", "evidence_ciphertext"}.isdisjoint(
        row.model_dump()
    )
    with Session(get_engine()) as session:
        links.verify_delivery_link(session, row)
        audit = session.exec(
            select(AuthorityAuditEvent).where(
                AuthorityAuditEvent.resource_type == "visit_prep_link",
                AuthorityAuditEvent.resource_ref == str(row.id),
            )
        ).one()
        assert receipt.access_token not in repr(audit.model_dump())
    current = _lineage(fixture.prep_id)[2][-1]
    assert (
        current.id == original.id
        and current.token_hash == original.token_hash
        and current.revoked_at is None
    )
    assert (
        interviews.get_interview(access_token=receipt.access_token).status
        == "confirmation_required"
    )
    assert (
        interviews.get_interview(access_token=fixture.access_token).status
        == "confirmation_required"
    )


@pytest.mark.parametrize("role", ["patient", "caregiver"])
def test_alias_confirms_once_and_new_reminder_uses_same_cookie(
    monkeypatch, owned_rate_limit_redis, role
):
    fixture = _fixture(monkeypatch, "alias-http", recipient_role=role)
    initial = links.issue_delivery_link(**_arguments(fixture, _intent(fixture)))
    headers = {"Authorization": f"Bearer {initial.access_token}"} | CONTINUED
    client = _client(monkeypatch, owned_rate_limit_redis)
    assert (
        client.get(PREFIX, headers=headers).json()["status"] == "confirmation_required"
    )
    confirmed = client.post(
        PREFIX + "/confirm", headers=headers, json={"first_name": "José", "role": role}
    )
    assert confirmed.status_code == 200
    proof = open_confirmation(client.cookies.get(COOKIE_NAME))
    assert proof.token_hash == access_token_hash(fixture.access_token)
    reminder = links.issue_delivery_link(**_arguments(fixture, _intent(fixture)))
    reminder_headers = {"Authorization": f"Bearer {reminder.access_token}"} | CONTINUED
    resumed = client.get(PREFIX, headers=reminder_headers)
    assert resumed.status_code == 200 and resumed.json()["continued_role"] == role
    assert client.get(PREFIX, headers=CONTINUED).json()["continued_role"] == role
    copied = _client(monkeypatch, owned_rate_limit_redis)
    stranger = copied.get(PREFIX, headers=reminder_headers)
    assert (
        stranger.json()["status"] == "confirmation_required"
        and stranger.json()["continued_role"] is None
    )


@pytest.mark.parametrize(
    "change",
    [
        "missing_source",
        "false_source",
        "view_as",
        "wrong_subject",
        "expired",
        "overlong",
        "completed",
        "late_baa",
    ],
)
def test_missing_or_late_authority_creates_no_alias(monkeypatch, change):
    fixture = _fixture(monkeypatch, "alias-deny")
    intent = _intent(fixture)
    arguments = _arguments(fixture, intent)
    if change == "missing_source":
        arguments.pop("source_verifier")
    elif change == "false_source":
        arguments["source_verifier"] = lambda *args, **kw: False
    elif change == "view_as":
        arguments["view_as_active"] = True
    elif change == "wrong_subject":
        arguments["expected_clerk_subject"] = "user_other"
    elif change in {"expired", "overlong"}:
        arguments["intent"] = replace(
            intent,
            expires_at=datetime.now(UTC)
            + timedelta(hours=-1 if change == "expired" else 25),
        )
    elif change == "completed":
        # Original fixture is untouched; a completed root is not new-send eligible.
        from tests.services.visit_prep_contribution_projection_test import _prepared

        fixture = _prepared(monkeypatch, "alias-completed")
        arguments = _arguments(fixture, _intent(fixture))
    elif change == "late_baa":

        def withdraw(session, candidate, **kw):
            practice = session.get(Practice, fixture.practice.id)
            practice.negotiated_baa_on_file = False
            session.add(practice)
            session.flush()
            return True

        arguments["source_verifier"] = withdraw
    _deny(arguments)
    assert not _rows(fixture)


def test_same_operation_concurrent_once_and_changed_deadline_conflicts(monkeypatch):
    fixture = _fixture(monkeypatch, "alias-concurrent")
    intent = _intent(fixture)
    arguments = _arguments(fixture, intent)
    barrier = Barrier(2)

    def issue():
        barrier.wait(timeout=10)
        return links.issue_delivery_link(**arguments)

    with ThreadPoolExecutor(max_workers=2) as pool:
        receipts = list(pool.map(lambda _: issue(), range(2)))
    assert sorted(r.disposition for r in receipts) == ["issued", "replayed"]
    assert sum(r.access_token is not None for r in receipts) == 1
    _deny(
        arguments
        | {
            "intent": replace(
                intent, expires_at=intent.expires_at + timedelta(minutes=1)
            )
        }
    )
    assert len(_rows(fixture)) == 1


@pytest.mark.parametrize(
    "action", ["rotate", "reschedule", "wrong_recipient", "expiry"]
)
def test_alias_never_survives_revoked_generation_or_its_deadline(monkeypatch, action):
    fixture = _fixture(monkeypatch, "alias-stale")
    intent = (
        _intent(fixture, expires_at=datetime.now(UTC) + timedelta(seconds=1))
        if action == "expiry"
        else _intent(fixture)
    )
    receipt = links.issue_delivery_link(**_arguments(fixture, intent))
    if action == "rotate":
        rotate_visit_prep_credential(
            owner_practice_member_id=fixture.owner.id,
            owner_doctor_id=fixture.doctor.id,
            practice_id=fixture.practice.id,
            visit_prep_id=fixture.prep_id,
            expected_credential_generation=1,
        )
    elif action == "reschedule":
        reschedule_visit_prep(
            owner_practice_member_id=fixture.owner.id,
            owner_doctor_id=fixture.doctor.id,
            practice_id=fixture.practice.id,
            visit_prep_id=fixture.prep_id,
            expected_credential_generation=1,
            scheduled_at=fixture.scheduled_at + timedelta(days=1),
            expected_schedule_generation=1,
            timezone_name="UTC",
        )
    elif action == "wrong_recipient":
        interviews.report_wrong_recipient(access_token=receipt.access_token)
    else:
        with Session(get_engine()) as session:
            session.execute(text("SELECT pg_sleep(1.1)"))
    with pytest.raises(interviews.VisitPrepInterviewUnavailable):
        interviews.get_interview(access_token=receipt.access_token)


def test_real_sql_cannot_mutate_delete_truncate_or_reuse_canonical_hash(monkeypatch):
    fixture = _fixture(monkeypatch, "alias-guard")
    links.issue_delivery_link(**_arguments(fixture, _intent(fixture)))
    row = _rows(fixture)[0]
    for statement in (
        "UPDATE visit_prep_delivery_link SET expires_at=expires_at WHERE id=:id",
        "DELETE FROM visit_prep_delivery_link WHERE id=:id",
        "TRUNCATE visit_prep_delivery_link",
    ):
        with Session(get_engine()) as session, pytest.raises(DBAPIError):
            session.execute(text(statement), {"id": row.id.uuid})
            session.commit()
    with Session(get_engine()) as session, pytest.raises(DBAPIError):
        session.execute(
            text(
                "INSERT INTO visit_prep_delivery_link SELECT :new_id,visit_prep_id,owner_practice_member_id,question_set_version_id,credential_id,:operation,:token,payload_hmac,evidence_ciphertext,envelope_format_version,issued_at,expires_at FROM visit_prep_delivery_link WHERE id=:id"
            ),
            {
                "new_id": uuid4(),
                "operation": uuid4(),
                "token": access_token_hash(fixture.access_token),
                "id": row.id.uuid,
            },
        )
        session.commit()


def test_atomic_sms_handoff_replay_and_actual_worker_preserve_original_invitation(
    monkeypatch,
):
    fixture, options, _ = _sms_ready(monkeypatch)
    intent = _intent(fixture)
    arguments = _linked(options, intent)
    queued = enqueue_linked_sms(**arguments)
    assert queued.disposition == "queued"
    alias = _rows(fixture)[0]
    row = _row(queued)
    assert row.expires_at == alias.expires_at == intent.expires_at
    payload = decrypt_value(row.payload_ciphertext)
    assert fixture.access_token not in payload
    assert enqueue_linked_sms(**arguments).disposition == "replayed"
    assert len(_rows(fixture)) == 1
    sent = []
    assert (
        worker.run_one_sms(
            **_worker(
                options, transport=lambda message: sent.append(message) or "accepted"
            )
        )
        == "accepted"
    )
    assert len(sent) == 1 and sent[0].access_token != fixture.access_token
    assert (
        interviews.get_interview(access_token=sent[0].access_token).status
        != "unavailable"
    )
    assert _row(queued).payload_ciphertext is None
    assert _lineage(fixture.prep_id)[2][-1].token_hash == access_token_hash(
        fixture.access_token
    )
    assert enqueue_linked_sms(**arguments).disposition == "replayed"


def test_late_sms_enqueue_failure_rolls_back_alias_and_its_audit(monkeypatch):
    fixture, options, _ = _sms_ready(monkeypatch)

    def fail(*args, **kw):
        raise VisitPrepSMSUnavailable()

    monkeypatch.setattr(sms, "enqueue_locked_sms", fail)
    with pytest.raises(VisitPrepSMSUnavailable):
        enqueue_linked_sms(**_linked(options, _intent(fixture)))
    assert not _rows(fixture)
    with Session(get_engine()) as session:
        audits = session.exec(
            select(AuthorityAuditEvent).where(
                AuthorityAuditEvent.resource_type == "visit_prep_link"
            )
        ).all()
        assert all(
            {"kind": "visit_prep", "ref": str(fixture.prep_id)}
            not in audit.platform_refs
            for audit in audits
        )


def test_sms_missing_qualification_does_not_create_any_link(monkeypatch):
    fixture, options, _ = _sms_ready(monkeypatch)
    arguments = _linked(options, _intent(fixture)) | {"consent_verifier": None}
    with pytest.raises(VisitPrepSMSUnavailable):
        enqueue_linked_sms(**arguments)
    assert not _rows(fixture)


def test_confirmed_cookie_follows_new_alias_after_reschedule_not_old_alias(
    monkeypatch, owned_rate_limit_redis
):
    fixture = _fixture(monkeypatch, "alias-rescheduled-return")
    initial = links.issue_delivery_link(**_arguments(fixture, _intent(fixture)))
    client = _client(monkeypatch, owned_rate_limit_redis)
    old_headers = {"Authorization": f"Bearer {initial.access_token}"} | CONTINUED
    assert (
        client.post(
            PREFIX + "/confirm",
            headers=old_headers,
            json={"first_name": "José", "role": "patient"},
        ).status_code
        == 200
    )
    reschedule_visit_prep(
        owner_practice_member_id=fixture.owner.id,
        owner_doctor_id=fixture.doctor.id,
        practice_id=fixture.practice.id,
        visit_prep_id=fixture.prep_id,
        expected_credential_generation=1,
        expected_schedule_generation=1,
        scheduled_at=fixture.scheduled_at + timedelta(days=1),
        timezone_name="UTC",
    )
    fresh = links.issue_delivery_link(**_arguments(fixture, _intent(fixture)))
    new_headers = {"Authorization": f"Bearer {fresh.access_token}"} | CONTINUED
    assert client.get(PREFIX, headers=old_headers).status_code == 404
    resumed = client.get(PREFIX, headers=new_headers)
    assert resumed.status_code == 200 and resumed.json()["continued_role"] == "patient"


def test_owned_transaction_commit_failure_never_returns_bearer(monkeypatch):
    fixture = _fixture(monkeypatch, "alias-lost-ack")
    intent = _intent(fixture)
    real_commit = Session.commit

    def lost_ack(session):
        real_commit(session)
        raise RuntimeError("synthetic lost acknowledgment")

    with monkeypatch.context() as context:
        context.setattr(Session, "commit", lost_ack)
        _deny(_arguments(fixture, intent))
    assert len(_rows(fixture)) == 1
    replay = links.issue_delivery_link(**_arguments(fixture, intent))
    assert replay.disposition == "replayed" and replay.access_token is None


def test_sms_lost_commit_ack_replays_without_restoring_payload_or_reminting(
    monkeypatch,
):
    fixture, options, _ = _sms_ready(monkeypatch)
    arguments = _linked(options, _intent(fixture))
    real_commit = Session.commit

    def lost_ack(session):
        real_commit(session)
        raise RuntimeError("synthetic lost acknowledgment")

    with monkeypatch.context() as context:
        context.setattr(Session, "commit", lost_ack)
        with pytest.raises(VisitPrepSMSUnavailable):
            enqueue_linked_sms(**arguments)
    before = _rows(fixture)[0]
    replay = enqueue_linked_sms(**arguments)
    assert replay.disposition == "replayed"
    assert (
        len(_rows(fixture)) == 1
        and _rows(fixture)[0].evidence_ciphertext == before.evidence_ciphertext
    )
    assert (
        worker.run_one_sms(**_worker(options, transport=lambda message: "accepted"))
        == "accepted"
    )
    assert _row(replay).payload_ciphertext is None
    assert enqueue_linked_sms(**arguments).disposition == "replayed"
    assert _row(replay).payload_ciphertext is None


def test_patient_can_update_story_after_short_link_expires_with_original_preserved(
    monkeypatch, owned_rate_limit_redis
):
    from tests.services.visit_prep_interviews_test import _respond
    from tests.services.visit_prep_packages_test import _read

    fixture = _fixture(monkeypatch, "alias-expired-remembered")
    receipt = links.issue_delivery_link(
        **_arguments(
            fixture,
            _intent(fixture, expires_at=datetime.now(UTC) + timedelta(seconds=2)),
        )
    )
    client = _client(monkeypatch, owned_rate_limit_redis)
    headers = {"Authorization": f"Bearer {receipt.access_token}"} | CONTINUED
    assert (
        client.post(
            PREFIX + "/confirm",
            headers=headers,
            json={"first_name": "José", "role": "patient"},
        ).status_code
        == 200
    )
    _respond(fixture, "reason", answer="My invented visit reason.")
    _respond(fixture, "medications")
    _respond(fixture, "adherence", answer="some")
    _respond(fixture, "adherence_barrier", answer="My invented barrier.")
    _respond(fixture, "story", answer="My original invented story.")
    interviews.complete_interview(access_token=fixture.access_token)
    with Session(get_engine()) as session:
        session.execute(
            text(
                "SELECT pg_sleep(GREATEST(0,EXTRACT(EPOCH FROM (:expiry - clock_timestamp()))) + 0.1)"
            ),
            {"expiry": receipt.expires_at},
        )
    assert client.get(PREFIX, headers=headers).status_code == 404
    before = _read(fixture)
    confirmed_at = _lineage(fixture.prep_id)[0].confirmed_at
    contribution = client.post(
        PREFIX + "/contribution", headers=CONTINUED, json={"role": "patient"}
    )
    assert contribution.status_code == 200
    changed = client.post(
        PREFIX + "/contribution/response",
        headers=CONTINUED | {"Idempotency-Key": str(uuid4())},
        json={
            "role": "patient",
            "basis": contribution.json()["basis"],
            "question_key": "story",
            "response_kind": "answered",
            "answer": "My updated invented story.",
        },
    )
    assert changed.status_code == 200
    assert (
        client.post(
            PREFIX + "/contribution/review",
            headers=CONTINUED | {"Idempotency-Key": str(uuid4())},
            json={"role": "patient", "basis": changed.json()["basis"]},
        ).status_code
        == 200
    )
    after = _read(fixture)
    assert after.source_version != before.source_version
    assert after.contribution.current_answers_reviewed
    story = after.contribution.topics[-1]
    assert (
        story.answer == "My updated invented story."
        and story.original_answer == "My original invented story."
    )
    assert _lineage(fixture.prep_id)[0].confirmed_at == confirmed_at
