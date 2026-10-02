"""Sender-wide STOP over real owned PostgreSQL; no provider or live messages."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.configuration import visit_prep_sms
from app.configuration.visit_prep_sms import VisitPrepSMSRouting, load_sms_routing
from app.services import visit_prep_sms_suppression as stops
from app.services.visit_prep_sms_suppression import SMSSuppressionUnavailable
from app.utils.encryption import decrypt_value, pseudonymous_digest

from activemodel.session_manager import get_engine
from app.models.authority import AuthorityAuditEvent
from app.models.visit_prep_sms_stop import VisitPrepSMSStopEvent
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlmodel import Session, select

from tests.integration.visit_prep_patient_continuation_http_test import (
    CONTINUED,
    PREFIX,
)
from tests.integration.visit_prep_reschedule_continuity_http_test import _move
from tests.integration.visit_prep_sms_consent_test import (
    _choose,
    _payload,
    _ready,
    _view,
)


def _routing(monkeypatch):
    routing = VisitPrepSMSRouting("synthetic", f"account-{uuid4()}", "+12025550100")
    monkeypatch.setattr(visit_prep_sms, "ROUTING", routing)
    return routing


def _event(routing, phone="+12025550123"):
    return stops.NormalizedSMSStop(
        stops.sms_sender_digest(routing),
        stops.sms_recipient_digest(phone),
        pseudonymous_digest(str(uuid4()), context="synthetic-sms-provider-event"),
        pseudonymous_digest("verified-synthetic-stop", context="synthetic-sms-source"),
        datetime.now(UTC) - timedelta(seconds=1),
    )


def _record(event):
    # Deliberately synthetic: proves domain behavior, NOT provider authentication.
    return stops.record_verified_sms_stop(
        event=event, source_verifier=lambda s, e, **kw: e == event
    )


def _suppressed(event):
    with Session(get_engine()) as session:
        return stops.read_locked_sms_suppression(
            session,
            sender_digest=event.sender_digest,
            recipient_digest=event.recipient_digest,
        )


def test_unknown_recipient_stop_persists_and_exact_replay_does_not_duplicate(
    monkeypatch,
):
    routing = _routing(monkeypatch)
    event = _event(routing)
    assert not _suppressed(event)
    first = _record(event)
    replay = _record(event)
    assert first.disposition == "recorded" and replay.disposition == "replayed"
    assert first.event_id == replay.event_id and first.recorded_at == replay.recorded_at
    assert _suppressed(event)
    assert not _suppressed(
        replace(event, recipient_digest=stops.sms_recipient_digest("+12025550124"))
    )
    assert not _suppressed(
        replace(
            event,
            sender_digest=stops.sms_sender_digest(
                replace(routing, sender_number="+12025550101")
            ),
        )
    )
    with pytest.raises(SMSSuppressionUnavailable):
        _record(replace(event, source_evidence_hmac="b" * 64))
    with pytest.raises(SMSSuppressionUnavailable):
        _record(
            replace(event, recipient_digest=stops.sms_recipient_digest("+12025550124"))
        )
    with Session(get_engine()) as session:
        rows = session.exec(
            select(VisitPrepSMSStopEvent).where(
                VisitPrepSMSStopEvent.sender_digest == event.sender_digest
            )
        ).all()
        assert len(rows) == 1
        row = rows[0]
        stops._verify(session, row)
        for private in (
            routing.account_reference,
            routing.sender_number,
            "+12025550123",
        ):
            assert private not in decrypt_value(row.evidence_ciphertext)
            assert private not in repr(event) and private not in repr(routing)
        assert row.recorded_at >= event.occurred_at


def test_stop_survives_new_preparation_and_reschedule_and_cannot_be_cleared(
    monkeypatch, owned_rate_limit_redis
):
    routing = _routing(monkeypatch)
    event = _event(routing)
    fixture, client = _ready(monkeypatch, owned_rate_limit_redis)
    first = _view(client).json()
    assert first["phone_suppression_status"] == "not_recorded"
    original, key = _payload(first), str(uuid4())
    assert _choose(client, original, key).status_code == 200
    _record(event)
    view = _view(client).json()
    assert view["status"] == "grant_recorded"
    assert view["phone_suppression_status"] == "stopped" and not view["can_grant"]
    # Exact historical receipt is harmless: it must not clear the terminal fence.
    assert _choose(client, original, key).status_code == 200
    assert _choose(client, _payload(view)).status_code == 409
    assert _choose(client, _payload(view, kind="withdrawn")).status_code == 200
    _move(fixture)
    after = _view(client).json()
    assert after["phone_suppression_status"] == "stopped" and not after["can_grant"]
    second, another = _ready(monkeypatch, owned_rate_limit_redis)
    assert (
        second.prep_id != fixture.prep_id and second.practice.id != fixture.practice.id
    )
    new_view = _view(another).json()
    assert (
        new_view["event_sequence"] == 0
        and new_view["phone_suppression_status"] == "stopped"
    )
    assert _choose(another, _payload(new_view)).status_code == 409
    email = another.post(
        PREFIX + "/email-consent", headers=CONTINUED, json={"role": "patient"}
    )
    assert email.status_code == 200 and email.json()["can_grant"]
    assert _suppressed(event)
    assert all(
        private not in str(new_view)
        for private in ("+12025550123", routing.sender_number, event.sender_digest)
    )


@pytest.mark.parametrize(
    "failure",
    ["missing", "false", "exception", "late", "future", "bad_digest", "bool_time"],
)
def test_untrusted_or_late_source_cannot_commit_stop_or_audit(monkeypatch, failure):
    event = _event(_routing(monkeypatch))
    with Session(get_engine()) as session:
        before = session.exec(
            select(AuthorityAuditEvent.id).where(
                AuthorityAuditEvent.resource_type == "visit_prep_sms_stop"
            )
        ).all()
    calls = []

    def verifier(session, observed, **kwargs):
        calls.append(1)
        if failure == "exception":
            raise RuntimeError("private provider data")
        return failure != "false" and not (failure == "late" and len(calls) == 2)

    value = (
        replace(event, occurred_at=datetime.now(UTC) + timedelta(days=1))
        if failure == "future"
        else event
    )
    if failure == "bad_digest":
        value = replace(event, recipient_digest="phone")
    if failure == "bool_time":
        value = replace(event, occurred_at=True)
    with pytest.raises(
        SMSSuppressionUnavailable, match="^SMS suppression is unavailable\\.$"
    ):
        stops.record_verified_sms_stop(
            event=value, source_verifier=None if failure == "missing" else verifier
        )
    with Session(get_engine()) as session:
        assert not session.exec(
            select(VisitPrepSMSStopEvent).where(
                VisitPrepSMSStopEvent.sender_digest == event.sender_digest
            )
        ).all()
        assert (
            session.exec(
                select(AuthorityAuditEvent.id).where(
                    AuthorityAuditEvent.resource_type == "visit_prep_sms_stop"
                )
            ).all()
            == before
        )


def test_shared_lock_blocks_both_record_and_read_then_accepts_stop(monkeypatch):
    event = _event(_routing(monkeypatch))
    with Session(get_engine()) as held:
        assert not stops.read_locked_sms_suppression(
            held,
            sender_digest=event.sender_digest,
            recipient_digest=event.recipient_digest,
        )
        with pytest.raises(SMSSuppressionUnavailable):
            _record(event)
        with pytest.raises(SMSSuppressionUnavailable):
            _suppressed(event)
        held.rollback()
    assert _record(event).disposition == "recorded" and _suppressed(event)


def test_repeatable_read_cannot_return_a_stale_negative(monkeypatch):
    event = _event(_routing(monkeypatch))
    with Session(
        get_engine().execution_options(isolation_level="REPEATABLE READ")
    ) as session:
        with pytest.raises(SMSSuppressionUnavailable):
            stops.read_locked_sms_suppression(
                session,
                sender_digest=event.sender_digest,
                recipient_digest=event.recipient_digest,
            )


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE public.visit_prep_sms_stop_event SET payload_hmac = payload_hmac WHERE id=:id",
        "DELETE FROM public.visit_prep_sms_stop_event WHERE id=:id",
        "TRUNCATE public.visit_prep_sms_stop_event",
    ],
)
def test_raw_sql_cannot_clear_stop(monkeypatch, sql):
    event = _event(_routing(monkeypatch))
    _record(event)
    with Session(get_engine()) as session:
        row = session.exec(
            select(VisitPrepSMSStopEvent).where(
                VisitPrepSMSStopEvent.sender_digest == event.sender_digest
            )
        ).one()
        with pytest.raises(DBAPIError):
            session.execute(text(sql), {"id": row.id.uuid})
        session.rollback()
    assert _suppressed(event)


def test_routing_unset_is_honest_and_partial_config_denies_sms_view(
    monkeypatch, owned_rate_limit_redis
):
    assert load_sms_routing({}) == VisitPrepSMSRouting()
    fixture, client = _ready(monkeypatch, owned_rate_limit_redis)
    monkeypatch.setattr(visit_prep_sms, "ROUTING", VisitPrepSMSRouting())
    assert _view(client).json()["phone_suppression_status"] == "not_connected"
    monkeypatch.setattr(visit_prep_sms, "ROUTING", VisitPrepSMSRouting("synthetic"))
    assert _view(client).status_code == 409
    assert "+12025550123" not in _view(client).text
    with pytest.raises(SMSSuppressionUnavailable):
        stops.sms_recipient_digest("202 555 0123")
