"""Real public proposal/decision API; synthetic evidence, no model qualification."""

import base64
import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from itertools import count
from threading import Event
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from itsdangerous import TimestampSigner

import app.routes.visit_prep_interviews as route
import app.services.visit_prep_clarification_decisions as commands
import app.services.visit_prep_clarification_view as views
from app.configuration.visit_prep import VisitPrepSettings
from app.routes.middleware import SESSION_SECRET_KEY, allowed_hosts
from app.server import api_app
from app.services import visit_prep_interviews as interviews
from app.services.visit_prep_clarification_contract import (
    VisitPrepClarificationInvalid,
    validate_clarification_reply,
)
from app.services.visit_preps import rotate_visit_prep_credential

from activemodel.session_manager import get_session, global_session
from app.models.authority import AuthorityAuditEvent
from app.models.practice import Practice
from app.models.visit_prep import VisitPrep
from sqlalchemy import text
from sqlmodel import Session, select

from tests.integration.visit_prep_clarification_decisions_test import _counts
from tests.integration.visit_prep_contribution_lock_test import _observe_waiting_lock
from tests.services.visit_prep_clarification_evidence_test import _decision, _proposal
from tests.services.visit_prep_contribution_commands_test import _correct
from tests.services.visit_prep_contribution_projection_test import (
    _prepared,
    _projection,
    _topic,
)

PREFIX = "/external/v1/visit-prep/clarification"
_PEERS = count(1)


def _client(monkeypatch, redis):
    monkeypatch.setattr(route.rate_limits, "get_rate_limit_redis", lambda: redis)
    return TestClient(
        api_app,
        base_url=allowed_hosts(with_scheme=True)[0],
        client=(f"198.18.0.{next(_PEERS)}", 50000),
    )


def _headers(fixture, key=None):
    return {
        "Authorization": f"Bearer {fixture.access_token}",
        **({"Idempotency-Key": key} if key else {}),
    }


def _read(client, fixture, role="patient", key="story"):
    return client.post(
        PREFIX,
        headers=_headers(fixture),
        json=dict(first_name="José", role=role, question_key=key),
    )


def _audits(session, fixture):
    return session.exec(
        select(AuthorityAuditEvent).where(
            AuthorityAuditEvent.event_type == "visit_prep.clarification_read",
            AuthorityAuditEvent.resource_ref == str(fixture.prep_id),
        )
    ).all()


def _private(response, fixture):
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["Referrer-Policy"] == "no-referrer"
    for value in (
        fixture.access_token,
        str(fixture.prep_id),
        str(fixture.owner.id),
        "patient@example.com",
        "José",
        "ciphertext",
        "source_version",
        "model_profile",
        "current_source_ref",
    ):
        assert value not in response.text


@pytest.mark.parametrize("role", ["patient", "caregiver"])
@pytest.mark.parametrize("action", ["confirm", "edit", "keep_original", "dismiss"])
def test_saved_proposal_read_explicit_choice_and_exact_replay(
    monkeypatch, owned_rate_limit_redis, role, action
):
    fixture, evidence, row = _proposal(monkeypatch, role=role)
    row.save()
    client = _client(monkeypatch, owned_rate_limit_redis)
    response = _read(client, fixture, role)
    assert response.status_code == 200
    view = response.json()
    assert view["status"] == "available" and view["proposal_id"] == evidence.proposal_id
    assert view["current_words"] == evidence.context.answer
    assert view["organized_words"] == validate_clarification_reply(
        evidence.context, evidence.reply
    )
    assert view["turns"][0]["patient_words"] == evidence.context.turns[0].patient_words
    key = str(uuid4())
    payload = dict(
        first_name="José",
        role=role,
        proposal_id=view["proposal_id"],
        action=action,
        edited_words="Synthetic patient-edited story." if action == "edit" else None,
    )
    saved = client.post(
        PREFIX + "/decision", headers=_headers(fixture, key), json=payload
    )
    replay = client.post(
        PREFIX + "/decision", headers=_headers(fixture, key), json=payload
    )
    assert saved.status_code == replay.status_code == 200
    assert (
        saved.json()["disposition"] == "accepted"
        and replay.json()["disposition"] == "replayed"
    )
    assert saved.json()["decision_id"] == replay.json()["decision_id"]
    assert "words" not in saved.text
    current = _topic(_projection(fixture), "story")
    assert current.original_answer == evidence.context.answer
    expected = (
        payload["edited_words"]
        if action == "edit"
        else view["organized_words"]
        if action == "confirm"
        else evidence.context.answer
    )
    assert current.answer == expected
    unavailable = _read(client, fixture, role)
    assert (
        unavailable.status_code == 200
        and unavailable.json()["status"] == "not_available"
    )
    assert (
        unavailable.json()["proposal_id"]
        is unavailable.json()["organized_words"]
        is None
    )
    assert unavailable.json()["turns"] == []
    with get_session() as session:
        assert _counts(session, fixture) == (1, int(action in {"confirm", "edit"}), 1)
        assert len(_audits(session, fixture)) == 2
        assert all(
            a.actor_id is a.person_id is a.content_sha256 is a.request_digest is None
            and "Invented" not in repr(a)
            for a in _audits(session, fixture)
        )
    for result in (response, saved, replay, unavailable):
        _private(result, fixture)


@pytest.mark.parametrize("suffix", ["", "/decision"])
@pytest.mark.parametrize(
    "failure", ["name", "role", "quarantine", "rotation", "baa", "disabled"]
)
def test_denied_current_authority_discloses_nothing(
    monkeypatch, owned_rate_limit_redis, suffix, failure
):
    if failure == "rotation":
        fixture = _prepared(monkeypatch, "clarification-rotation-http", complete=False)
        fixture, evidence, row = _proposal(monkeypatch, fixture, question="reason")
    else:
        fixture, evidence, row = _proposal(monkeypatch)
    row.save()
    if failure == "quarantine":
        interviews.report_wrong_recipient(access_token=fixture.access_token)
    elif failure == "rotation":
        rotate_visit_prep_credential(
            owner_practice_member_id=fixture.owner.id,
            owner_doctor_id=fixture.doctor.id,
            practice_id=fixture.practice.id,
            visit_prep_id=fixture.prep_id,
            expected_credential_generation=1,
        )
    elif failure == "baa":
        fixture.practice.negotiated_baa_on_file = False
        fixture.practice.save()
    elif failure == "disabled":
        monkeypatch.setattr(
            interviews.visit_prep_access, "VISIT_PREP_SETTINGS", VisitPrepSettings()
        )
    client = _client(monkeypatch, owned_rate_limit_redis)
    payload = dict(
        first_name="Wrong" if failure == "name" else "José",
        role="caregiver" if failure == "role" else "patient",
    )
    payload.update(
        dict(proposal_id=evidence.proposal_id, action="confirm")
        if suffix
        else dict(question_key="story")
    )
    response = client.post(
        PREFIX + suffix,
        headers=_headers(fixture, str(uuid4()) if suffix else None),
        json=payload,
    )
    assert response.status_code == 404 and "Invented" not in response.text
    _private(response, fixture)
    with get_session() as session:
        assert not _audits(session, fixture) and _counts(session, fixture) == (0, 0, 0)


@pytest.mark.parametrize("question", ["reason", "adherence", "adherence_barrier"])
def test_never_disclose_other_topic_choice_or_hidden_proposal(
    monkeypatch, owned_rate_limit_redis, question
):
    fixture, _, row = _proposal(monkeypatch)
    row.save()
    response = _read(
        _client(monkeypatch, owned_rate_limit_redis), fixture, key=question
    )
    assert response.status_code == (404 if question == "adherence_barrier" else 200)
    assert "Invented" not in response.text
    if response.status_code == 200:
        assert response.json()["status"] == "not_available"
    _private(response, fixture)


def test_stale_proposal_not_read_and_old_choice_conflicts(
    monkeypatch, owned_rate_limit_redis
):
    fixture, evidence, row = _proposal(monkeypatch)
    row.save()
    _correct(fixture, question="story", answer="Synthetic changed story.")
    client = _client(monkeypatch, owned_rate_limit_redis)
    response = _read(client, fixture)
    assert response.status_code == 200 and response.json()["status"] == "not_available"
    rejected = client.post(
        PREFIX + "/decision",
        headers=_headers(fixture, str(uuid4())),
        json=dict(
            first_name="José",
            role="patient",
            proposal_id=evidence.proposal_id,
            action="confirm",
        ),
    )
    assert rejected.status_code == 409
    assert "Invented" not in response.text + rejected.text


@pytest.mark.parametrize("suffix", ["", "/decision"])
@pytest.mark.parametrize("failure", ["get", "query", "extra", "large", "key"])
def test_public_privacy_body_query_and_idempotency_boundary(
    monkeypatch, owned_rate_limit_redis, suffix, failure
):
    fixture, evidence, row = _proposal(monkeypatch)
    row.save()
    client = _client(monkeypatch, owned_rate_limit_redis)
    payload = dict(first_name="José", role="patient")
    payload.update(
        dict(proposal_id=evidence.proposal_id, action="confirm")
        if suffix
        else dict(question_key="story")
    )
    headers = _headers(fixture, str(uuid4()) if suffix else None)
    url = PREFIX + suffix
    if failure == "get":
        response = client.get(url, headers=headers)
    elif failure == "query":
        response = client.post(url + "?private=Invented", headers=headers, json=payload)
    elif failure == "large":
        response = client.post(url, headers=headers, content=b"x" * 10241)
    elif failure == "extra":
        response = client.post(
            url, headers=headers, json=payload | {"model_profile": "Invented"}
        )
    elif suffix:
        headers["Idempotency-Key"] = "not-canonical"
        response = client.post(url, headers=headers, json=payload)
    else:
        response = client.post(url, headers=headers, json=payload | {"question_key": 5})
    assert response.status_code in {404, 413, 422} and "Invented" not in response.text
    _private(response, fixture)
    with get_session() as session:
        assert not _audits(session, fixture) and _counts(session, fixture) == (0, 0, 0)


def test_reads_share_existing_confirmation_budget(monkeypatch, owned_rate_limit_redis):
    fixture, _, row = _proposal(monkeypatch)
    row.save()
    client = _client(monkeypatch, owned_rate_limit_redis)
    for _ in range(8):
        assert _read(client, fixture).status_code == 200
    limited = _read(client, fixture)
    assert limited.status_code == 429
    _private(limited, fixture)


def test_external_openapi_has_closed_post_contract_and_header_bearer():
    app = FastAPI()
    app.include_router(route.visit_prep_interviews_api_app)
    schema = app.openapi()
    for suffix in ("", "/decision"):
        operation = schema["paths"][PREFIX + suffix]
        assert set(operation) == {"post"}
        assert operation["post"]["security"] == [{"VisitPrepBearer": []}]
        assert operation["post"]["requestBody"]["required"]
    request = schema["components"]["schemas"]["VisitPrepClarificationDecisionRequest"]
    assert request["additionalProperties"] is False
    assert set(request["properties"]) == {
        "first_name",
        "role",
        "proposal_id",
        "action",
        "edited_words",
    }


@pytest.mark.parametrize("suffix", ["", "/decision"])
def test_support_view_cannot_read_or_decide(
    monkeypatch, owned_rate_limit_redis, suffix
):
    fixture, evidence, row = _proposal(monkeypatch)
    row.save()
    client = _client(monkeypatch, owned_rate_limit_redis)
    cookie = (
        TimestampSigner(str(SESSION_SECRET_KEY))
        .sign(
            base64.b64encode(
                json.dumps(
                    {
                        "view_as_session_id": "synthetic-view",
                        "view_as_browser_nonce": "synthetic-nonce",
                    }
                ).encode()
            )
        )
        .decode()
    )
    payload = dict(first_name="José", role="patient")
    payload.update(
        dict(proposal_id=evidence.proposal_id, action="confirm")
        if suffix
        else dict(question_key="story")
    )
    response = client.post(
        PREFIX + suffix,
        headers=_headers(fixture, str(uuid4()) if suffix else None)
        | {"Cookie": f"session={cookie}"},
        json=payload,
    )
    assert response.status_code == 404 and "Invented" not in response.text
    _private(response, fixture)
    with get_session() as session:
        assert not _audits(session, fixture) and _counts(session, fixture) == (0, 0, 0)


def test_latest_decided_proposal_does_not_reveal_an_older_alternative(monkeypatch):
    fixture, _, first = _proposal(monkeypatch)
    first.save()
    fixture, latest, row = _proposal(monkeypatch, fixture)
    row.save()
    _, decided = _decision(latest)
    decided.save()
    view = views.get_patient_clarification_view(
        access_token=fixture.access_token,
        first_name="José",
        role="patient",
        question_key="story",
    )
    assert view.status == "not_available" and view.proposal_id is None


def test_no_proposal_plaintext_is_loaded_before_recipient_authority(monkeypatch):
    fixture, _, row = _proposal(monkeypatch)
    row.save()

    def forbidden(*_args, **_kwargs):
        pytest.fail(
            "Proposal plaintext must not be read before recipient authorization"
        )

    monkeypatch.setattr(commands, "read_clarification_proposal", forbidden)
    with pytest.raises(VisitPrepClarificationInvalid):
        views.get_patient_clarification_view(
            access_token=fixture.access_token,
            first_name="Wrong",
            role="patient",
            question_key="story",
        )


def test_final_time_dependent_authority_denial_rolls_back_read(monkeypatch):
    fixture, _, row = _proposal(monkeypatch)
    row.save()
    original = commands._lock_authorized_interview
    expires = datetime.now(UTC) + timedelta(seconds=3)
    fixture.practice.billing_exempt_until = expires
    fixture.practice.save()
    calls = 0

    def clock_after_staging(session, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            session.execute(
                text(
                    "SELECT pg_sleep(GREATEST(0, EXTRACT(EPOCH FROM (:expiry - clock_timestamp()))) + 0.1)"
                ),
                {"expiry": expires},
            )
        return original(session, **kwargs)

    monkeypatch.setattr(commands, "_lock_authorized_interview", clock_after_staging)
    with pytest.raises(VisitPrepClarificationInvalid):
        views.get_patient_clarification_view(
            access_token=fixture.access_token,
            first_name="José",
            role="patient",
            question_key="story",
        )
    with get_session() as session:
        assert not _audits(session, fixture)


@pytest.mark.parametrize("failure", ["audit", "final_authority", "lost_ack"])
def test_read_commits_audit_before_disclosure_and_fails_privately(monkeypatch, failure):
    fixture, _, row = _proposal(monkeypatch)
    row.save()
    original_commit, original_auth, original_flush = (
        views._commit,
        views._authorized,
        Session.flush,
    )

    def deny(*_args, **_kwargs):
        raise VisitPrepClarificationInvalid()

    if failure == "audit":

        def fail_audit(session, *args, **kwargs):
            if any(isinstance(r, AuthorityAuditEvent) for r in session.new):
                deny()
            return original_flush(session, *args, **kwargs)

        monkeypatch.setattr(Session, "flush", fail_audit)
    elif failure == "final_authority":
        calls = 0

        def deny_after_staging(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                deny()
            return original_auth(*args, **kwargs)

        monkeypatch.setattr(views, "_authorized", deny_after_staging)
    else:

        def lost_ack(session):
            with get_session() as observer:
                assert not _audits(observer, fixture)
            original_commit(session)
            deny()

        monkeypatch.setattr(views, "_commit", lost_ack)
    with pytest.raises(VisitPrepClarificationInvalid) as caught:
        views.get_patient_clarification_view(
            access_token=fixture.access_token,
            first_name="José",
            role="patient",
            question_key="story",
        )
    assert caught.value.__cause__ is caught.value.__context__ is None
    with get_session() as session:
        assert len(_audits(session, fixture)) == int(failure == "lost_ack")


@pytest.mark.parametrize("change", ["correction", "baa"])
def test_read_rechecks_after_actual_authority_lock_wait(monkeypatch, change):
    fixture, _, row = _proposal(monkeypatch)
    row.save()
    original = commands._visit_prep_session
    started = Event()

    @contextmanager
    def named_session():
        with original() as session:
            session.execute(
                text("SET LOCAL application_name = 'vp_clarification_read_wait'")
            )
            started.set()
            yield session

    with global_session() as session:
        session.exec(
            select(Practice).where(Practice.id == fixture.practice.id).with_for_update()
        ).one()
        session.exec(
            select(VisitPrep).where(VisitPrep.id == fixture.prep_id).with_for_update()
        ).one()
        with monkeypatch.context() as patch:
            patch.setattr(commands, "_visit_prep_session", named_session)
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(
                    views.get_patient_clarification_view,
                    access_token=fixture.access_token,
                    first_name="José",
                    role="patient",
                    question_key="story",
                )
                try:
                    assert started.wait(5)
                    _observe_waiting_lock(session, "vp_clarification_read_wait")
                    if change == "correction":
                        _correct(
                            fixture,
                            question="story",
                            answer="Synthetic waiting correction.",
                        )
                    else:
                        fixture.practice.negotiated_baa_on_file = False
                        fixture.practice.save()
                finally:
                    session.commit()
                if change == "baa":
                    with pytest.raises(VisitPrepClarificationInvalid):
                        future.result(15)
                else:
                    assert future.result(15).status == "not_available"
