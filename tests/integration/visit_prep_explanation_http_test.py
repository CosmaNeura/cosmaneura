"""Real closed patient explanation HTTP path, with owned PostgreSQL/Redis."""

import base64
import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from itsdangerous import TimestampSigner

import app.services.visit_prep_interviews as interviews
from app.routes.middleware import SESSION_SECRET_KEY
from app.routes.visit_prep_packages import _source_response
from app.services.visit_prep_packages import get_visit_prep_package
from app.services.visit_prep_patient_continuation import COOKIE_NAME
from app.services.visit_preps import revoke_visit_prep

from activemodel.session_manager import get_session
from app.models.visit_prep_contribution import VisitPrepContributionEvent
from sqlalchemy import DateTime, func, literal
from sqlmodel import select

from tests.integration.visit_prep_contribution_http_test import _headers
from tests.integration.visit_prep_patient_continuation_http_test import (
    CONTINUED,
    PREFIX,
    _client,
    _confirmed,
)
from tests.services.visit_prep_contribution_projection_test import _projection


@pytest.mark.parametrize("role", ["patient", "caregiver"])
def test_cookie_only_explanation_is_confirmed_separate_and_provider_visible(
    monkeypatch, owned_rate_limit_redis, role
):
    fixture, client = _confirmed(monkeypatch, owned_rate_limit_redis, role=role)
    body = {
        "role": role,
        "basis": {"original_response_count": 4, "event_sequence": 0},
        "question_key": "adherence",
        "patient_words": "Invented patient explanation, not a new code.",
    }
    key = str(uuid4())
    response = client.post(
        PREFIX + "/contribution/explanation",
        json=body,
        headers=CONTINUED | {"Idempotency-Key": key},
    )
    assert (
        response.status_code == 200 and response.json()["basis"]["event_sequence"] == 1
    )
    assert "Invented patient" not in response.text
    assert "no-store" in response.headers["cache-control"]
    assert response.headers["referrer-policy"] == "no-referrer"
    retry = client.post(
        PREFIX + "/contribution/explanation",
        json=body,
        headers=CONTINUED | {"Idempotency-Key": key},
    )
    assert retry.status_code == 200 and retry.json()["disposition"] == "replayed"
    read = client.post(PREFIX + "/contribution", json={"role": role}, headers=CONTINUED)
    choice = next(
        t for t in read.json()["topics"] if t["question"]["key"] == "adherence"
    )
    assert (
        choice["answer"] == choice["initial_answer"] == "yes" and not choice["changed"]
    )
    assert choice["explanation_words"] == body["patient_words"]
    assert "explanation_source_id" not in choice
    package = get_visit_prep_package(
        owner_practice_member_id=fixture.owner.id,
        owner_doctor_id=fixture.doctor.id,
        practice_id=fixture.practice.id,
        expected_clerk_subject=fixture.doctor.clerk_id,
        visit_prep_id=fixture.prep_id,
    )
    provider = next(
        t for t in _source_response(package).topics if t.question_key == "adherence"
    )
    assert (
        provider.explanation_words == body["patient_words"]
        and provider.explanation_source_id
    )
    assert provider.original_response_id != provider.explanation_source_id


@pytest.mark.parametrize(
    "bad",
    [
        "no_cookie",
        "wrong_role",
        "no_name_raw",
        "extra",
        "large",
        "origin",
        "duplicate_key",
        "stale",
    ],
)
def test_explanation_boundary_denies_ambiguous_or_unproved_mutations_privately(
    monkeypatch, owned_rate_limit_redis, bad
):
    fixture, client = _confirmed(monkeypatch, owned_rate_limit_redis)
    headers = CONTINUED | {"Idempotency-Key": str(uuid4())}
    body = {
        "role": "patient",
        "basis": {"original_response_count": 4, "event_sequence": 0},
        "question_key": "adherence",
        "patient_words": "Invented private explanation.",
    }
    if bad == "no_cookie":
        client = _client(monkeypatch, owned_rate_limit_redis)
    elif bad == "wrong_role":
        body["role"] = "caregiver"
    elif bad == "no_name_raw":
        client.cookies.clear()
        headers = _headers(fixture) | {"Idempotency-Key": str(uuid4())}
    elif bad == "extra":
        body["actor_id"] = "Invented private caller."
    elif bad == "large":
        body["patient_words"] = "Invented private explanation." * 500
    elif bad == "origin":
        headers["Origin"] = "https://evil.invalid"
    elif bad == "duplicate_key":
        headers = list(headers.items()) + [("Idempotency-Key", str(uuid4()))]
    elif bad == "stale":
        body["basis"]["event_sequence"] = 1
    response = client.post(
        PREFIX + "/contribution/explanation", json=body, headers=headers
    )
    assert response.status_code in {404, 409, 413, 422}
    assert (
        "Invented private" not in response.text
        and fixture.access_token not in response.text
    )
    assert "no-store" in response.headers["cache-control"]
    assert _projection(fixture).basis.event_sequence == 0


@pytest.mark.parametrize(
    "gate", ["expire", "revoke", "wrong_recipient", "support_view"]
)
def test_explanation_rechecks_current_access_before_any_new_evidence(
    monkeypatch, owned_rate_limit_redis, gate
):
    fixture, client = _confirmed(monkeypatch, owned_rate_limit_redis)
    headers = CONTINUED | {"Idempotency-Key": str(uuid4())}
    if gate == "expire":
        future = datetime.now(UTC) + timedelta(days=30)
        monkeypatch.setattr(
            interviews.func,
            "clock_timestamp",
            lambda: literal(future, type_=DateTime(timezone=True)),
        )
    elif gate == "revoke":
        revoke_visit_prep(
            owner_practice_member_id=fixture.owner.id,
            owner_doctor_id=fixture.doctor.id,
            practice_id=fixture.practice.id,
            visit_prep_id=fixture.prep_id,
            expected_credential_generation=1,
            reason="physician_revoked",
        )
    elif gate == "wrong_recipient":
        assert (
            client.post(PREFIX + "/wrong-recipient", headers=CONTINUED).status_code
            == 200
        )
    else:
        payload = base64.b64encode(
            json.dumps(
                {
                    "view_as_session_id": "synthetic-view",
                    "view_as_browser_nonce": "synthetic-nonce",
                }
            ).encode()
        )
        signed = TimestampSigner(str(SESSION_SECRET_KEY)).sign(payload).decode()
        headers["Cookie"] = (
            f"session={signed}; {COOKIE_NAME}={client.cookies.get(COOKIE_NAME)}"
        )
    response = client.post(
        PREFIX + "/contribution/explanation",
        json={
            "role": "patient",
            "basis": {"original_response_count": 4, "event_sequence": 0},
            "question_key": "adherence",
            "patient_words": "Invented private explanation denied by current authority.",
        },
        headers=headers,
    )
    assert response.status_code == 404 and "Invented private" not in response.text
    assert "no-store" in response.headers["cache-control"]
    with get_session() as session:
        assert (
            session.exec(
                select(func.count())
                .select_from(VisitPrepContributionEvent)
                .where(VisitPrepContributionEvent.visit_prep_id == fixture.prep_id)
            ).one()
            == 0
        )
