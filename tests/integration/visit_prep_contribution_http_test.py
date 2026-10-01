"""Real gated patient contribution API with owned PostgreSQL/Redis and invented text."""

import base64
import json
from itertools import count
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from itsdangerous import TimestampSigner

import app.routes.visit_prep_interviews as route
from app.configuration.visit_prep import VisitPrepSettings
from app.routes.middleware import SESSION_SECRET_KEY, allowed_hosts
from app.server import api_app
from app.services import visit_prep_interviews as interviews

from activemodel.session_manager import get_session
from app.models.visit_prep import VisitPrep
from app.models.visit_prep_contribution import VisitPrepContributionEvent
from sqlmodel import select

from tests.services.visit_prep_contribution_projection_test import _prepared
from tests.services.visit_prep_interviews_test import _fixture

PREFIX = "/external/v1/visit-prep/contribution"
_PEERS = count(1)


def _client(monkeypatch, owned_rate_limit_redis):
    monkeypatch.setattr(
        route.rate_limits, "get_rate_limit_redis", lambda: owned_rate_limit_redis
    )
    # The disposable Redis belongs to the whole run. Give each independent
    # synthetic journey its own transport peer, while preserving the real
    # network/credential counters across all requests within that journey.
    return TestClient(
        api_app,
        base_url=allowed_hosts(with_scheme=True)[0],
        client=(f"198.51.100.{next(_PEERS)}", 50000),
    )


def _headers(fixture, *, key=None):
    return {
        "Authorization": f"Bearer {fixture.access_token}",
        **({"Idempotency-Key": key} if key else {}),
    }


def _confirm(role="patient"):
    return {"first_name": "José", "role": role}


def _assert_private(response, fixture):
    assert "no-store" in response.headers["Cache-Control"]
    assert response.headers["Referrer-Policy"] == "no-referrer"
    assert fixture.access_token not in response.text
    assert str(fixture.prep_id) not in response.text
    assert str(fixture.owner.id) not in response.text
    assert "ciphertext" not in response.text and "payload_hmac" not in response.text


@pytest.mark.parametrize("role", ["patient", "caregiver"])
def test_current_patient_can_review_correct_and_answer_newly_revealed_topic(
    monkeypatch, owned_rate_limit_redis, role
):
    fixture = _prepared(monkeypatch, "contribution-http", role=role)
    client = _client(monkeypatch, owned_rate_limit_redis)
    initial = client.post(PREFIX, headers=_headers(fixture), json=_confirm(role))
    assert initial.status_code == 200
    view = initial.json()
    assert view["basis"] == {"original_response_count": 4, "event_sequence": 0}
    assert view["actor_role"] == role and view["original_interview_completed"]
    assert view["current_answers_complete"] and not view["current_answers_reviewed"]
    assert [topic["question"]["key"] for topic in view["topics"]] == [
        "reason",
        "medications",
        "adherence",
        "story",
    ]
    story = view["topics"][-1]
    assert story["answer"] == story["initial_answer"] == "My invented health story."
    assert story["question"]["kind"] == "story" and not story["question"]["choices"]
    assert not story["changed"]
    review_headers = _headers(fixture, key=str(uuid4()))
    review_payload = _confirm(role) | {"basis": view["basis"]}
    accepted = client.post(
        PREFIX + "/review", headers=review_headers, json=review_payload
    )
    assert accepted.status_code == 200 and accepted.json()["current_answers_reviewed"]
    correction_headers = _headers(fixture, key=str(uuid4()))
    correction_payload = _confirm(role) | dict(
        basis=accepted.json()["basis"],
        question_key="adherence",
        response_kind="answered",
        answer="some",
    )
    changed = client.post(
        PREFIX + "/response", headers=correction_headers, json=correction_payload
    )
    assert changed.status_code == 200
    assert (
        not changed.json()["current_answers_complete"]
        and not changed.json()["current_answers_reviewed"]
    )
    assert set(changed.json()) == {
        "disposition",
        "basis",
        "current_answers_complete",
        "current_answers_reviewed",
    }
    assert "some" not in changed.text
    reread = client.post(PREFIX, headers=_headers(fixture), json=_confirm(role))
    assert reread.status_code == 200
    barrier = next(
        topic
        for topic in reread.json()["topics"]
        if topic["question"]["key"] == "adherence_barrier"
    )
    assert barrier["answer"] is barrier["initial_answer"] is None
    added = client.post(
        PREFIX + "/response",
        headers=_headers(fixture, key=str(uuid4())),
        json=_confirm(role)
        | dict(
            basis=reread.json()["basis"],
            question_key="adherence_barrier",
            response_kind="answered",
            answer="An invented new barrier.",
        ),
    )
    assert added.status_code == 200 and added.json()["current_answers_complete"]
    reviewed = client.post(
        PREFIX + "/review",
        headers=_headers(fixture, key=str(uuid4())),
        json=_confirm(role) | {"basis": added.json()["basis"]},
    )
    assert reviewed.status_code == 200 and reviewed.json()["current_answers_reviewed"]
    final = client.post(PREFIX, headers=_headers(fixture), json=_confirm(role))
    final_topic = next(
        topic
        for topic in final.json()["topics"]
        if topic["question"]["key"] == "adherence_barrier"
    )
    assert (
        final_topic["answer"]
        == final_topic["initial_answer"]
        == "An invented new barrier."
    )
    assert not final_topic["changed"]
    for response in (
        initial,
        accepted,
        changed,
        reread,
        added,
        reviewed,
        final,
    ):
        _assert_private(response, fixture)


def test_same_key_retry_divergence_and_stale_review_use_current_state(
    monkeypatch, owned_rate_limit_redis
):
    fixture = _prepared(monkeypatch, "contribution-http-replay")
    client = _client(monkeypatch, owned_rate_limit_redis)
    basis = dict(original_response_count=4, event_sequence=0)
    review_headers = _headers(fixture, key=str(uuid4()))
    review_payload = _confirm() | {"basis": basis}
    reviewed = client.post(
        PREFIX + "/review", headers=review_headers, json=review_payload
    )
    assert reviewed.status_code == 200
    correction_headers = _headers(fixture, key=str(uuid4()))
    correction_payload = _confirm() | dict(
        basis=reviewed.json()["basis"],
        question_key="adherence",
        response_kind="answered",
        answer="some",
    )
    changed = client.post(
        PREFIX + "/response", headers=correction_headers, json=correction_payload
    )
    repeated = client.post(
        PREFIX + "/response", headers=correction_headers, json=correction_payload
    )
    assert changed.status_code == repeated.status_code == 200
    assert repeated.json()["disposition"] == "replayed"
    divergent = client.post(
        PREFIX + "/response",
        headers=correction_headers,
        json=correction_payload | {"answer": "no"},
    )
    assert divergent.status_code == 409
    stale = client.post(PREFIX + "/review", headers=review_headers, json=review_payload)
    assert stale.status_code == 200 and not stale.json()["current_answers_reviewed"]
    incomplete = client.post(
        PREFIX + "/review",
        headers=_headers(fixture, key=str(uuid4())),
        json=_confirm() | {"basis": changed.json()["basis"]},
    )
    assert incomplete.status_code == 409
    for response in (reviewed, changed, repeated, divergent, stale, incomplete):
        _assert_private(response, fixture)


def test_saved_answer_get_and_wrong_confirmation_never_disclose_patient_text(
    monkeypatch, owned_rate_limit_redis
):
    fixture = _prepared(monkeypatch, "contribution-http-denial")
    client = _client(monkeypatch, owned_rate_limit_redis)
    denied = [client.get(PREFIX, headers=_headers(fixture))]
    for confirmation in (
        {"first_name": "Wrong invented name", "role": "patient"},
        {"first_name": "José", "role": "caregiver"},
    ):
        for suffix, extra in (
            ("", {}),
            (
                "/response",
                dict(
                    basis=dict(original_response_count=4, event_sequence=0),
                    question_key="story",
                    response_kind="answered",
                    answer="An invented denied correction.",
                ),
            ),
            ("/review", dict(basis=dict(original_response_count=4, event_sequence=0))),
        ):
            denied.append(
                client.post(
                    PREFIX + suffix,
                    headers=_headers(fixture, key=str(uuid4())),
                    json=confirmation | extra,
                )
            )
    for response in denied:
        assert response.status_code == 404
        assert response.json() == {"detail": "Visit Prep link is unavailable."}
        assert "invented" not in response.text
        _assert_private(response, fixture)
    with get_session() as session:
        assert not session.exec(
            select(VisitPrepContributionEvent).where(
                VisitPrepContributionEvent.visit_prep_id == fixture.prep_id
            )
        ).all()


def test_hidden_retained_answer_never_leaves_patient_review_wire(
    monkeypatch, owned_rate_limit_redis
):
    from tests.services.visit_prep_contribution_commands_test import _correct

    fixture = _prepared(monkeypatch, "contribution-http-hidden", adherence="some")
    _correct(fixture, question="adherence", answer="yes")
    client = _client(monkeypatch, owned_rate_limit_redis)
    response = client.post(PREFIX, headers=_headers(fixture), json=_confirm())
    assert response.status_code == 200
    assert (
        "adherence_barrier" not in response.text
        and "An invented barrier." not in response.text
    )
    topic = next(
        row
        for row in response.json()["topics"]
        if row["question"]["key"] == "adherence"
    )
    assert (
        topic["answer"] == "yes"
        and topic["initial_answer"] == "some"
        and topic["changed"]
    )
    _assert_private(response, fixture)


@pytest.mark.parametrize("state", ["unconfirmed", "quarantined", "gate_off"])
def test_current_state_denies_contribution_read(
    monkeypatch, owned_rate_limit_redis, state
):
    fixture = (
        _fixture(monkeypatch, "contribution-http-unconfirmed")
        if state == "unconfirmed"
        else _prepared(monkeypatch, "contribution-http-current-state")
    )
    if state == "quarantined":
        interviews.report_wrong_recipient(access_token=fixture.access_token)
    elif state == "gate_off":
        monkeypatch.setattr(
            interviews.visit_prep_access, "VISIT_PREP_SETTINGS", VisitPrepSettings()
        )
    client = _client(monkeypatch, owned_rate_limit_redis)
    response = client.post(PREFIX, headers=_headers(fixture), json=_confirm())
    assert response.status_code == 404 and "invented" not in response.text
    _assert_private(response, fixture)


@pytest.mark.parametrize("suffix", ["", "/response", "/review"])
def test_support_view_denies_all_patient_contribution_operations(
    monkeypatch, owned_rate_limit_redis, suffix
):
    fixture = _prepared(monkeypatch, "contribution-http-support")
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
    payload = _confirm() | (
        {"basis": {"original_response_count": 4, "event_sequence": 0}} if suffix else {}
    )
    if suffix == "/response":
        payload |= {"question_key": "story", "response_kind": "skipped", "answer": None}
    response = client.post(
        PREFIX + suffix,
        headers=_headers(fixture, key=str(uuid4())) | {"Cookie": f"session={cookie}"},
        json=payload,
    )
    assert response.status_code == 404 and "invented" not in response.text
    _assert_private(response, fixture)
    with get_session() as session:
        prep = session.get(VisitPrep, fixture.prep_id)
        assert prep is not None and prep.revoked_at is None


@pytest.mark.parametrize(
    "extra",
    [
        {"basis": {"original_response_count": True, "event_sequence": 0}},
        {"basis": {"original_response_count": 4, "event_sequence": "0"}},
        {"basis": {"original_response_count": 4, "event_sequence": -1}},
        {"answer": "An invented overlength answer" * 100},
        {"owner_practice_member_id": "private-owner"},
    ],
)
def test_patient_change_request_is_strict_and_validation_output_is_redacted(
    monkeypatch, owned_rate_limit_redis, extra
):
    fixture = _prepared(monkeypatch, "contribution-http-validation")
    client = _client(monkeypatch, owned_rate_limit_redis)
    payload = (
        _confirm()
        | dict(
            basis=dict(original_response_count=4, event_sequence=0),
            question_key="story",
            response_kind="answered",
            answer="An invented private draft.",
        )
        | extra
    )
    response = client.post(
        PREFIX + "/response", headers=_headers(fixture, key=str(uuid4())), json=payload
    )
    assert response.status_code == 422
    assert response.json() == {"detail": "Visit Prep request is invalid."}
    _assert_private(response, fixture)


@pytest.mark.parametrize("suffix", ["", "/response", "/review"])
def test_all_contribution_confirmation_uses_existing_bruteforce_limits(
    monkeypatch, owned_rate_limit_redis, suffix
):
    fixture = _prepared(monkeypatch, "contribution-http-rate")
    client = _client(monkeypatch, owned_rate_limit_redis)
    extra = (
        {"basis": {"original_response_count": 4, "event_sequence": 0}} if suffix else {}
    )
    if suffix == "/response":
        extra |= {"question_key": "story", "response_kind": "skipped", "answer": None}
    for _ in range(route.rate_limits.CONFIRMATION_LIMIT):
        response = client.post(
            PREFIX + suffix,
            headers=_headers(fixture, key=str(uuid4())),
            json={"first_name": "Wrong invented name", "role": "patient"} | extra,
        )
        assert response.status_code == 404
    limited = client.post(
        PREFIX + suffix,
        headers=_headers(fixture, key=str(uuid4())),
        json=_confirm() | extra,
    )
    assert limited.status_code == 429
    assert limited.headers["Retry-After"] == str(route.rate_limits.WINDOW_SECONDS)
    _assert_private(limited, fixture)
