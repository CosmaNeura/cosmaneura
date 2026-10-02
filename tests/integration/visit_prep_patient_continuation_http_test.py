"""One initial check then real remembered return/edit; owned PostgreSQL/Redis."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from itsdangerous import TimestampSigner

import app.routes.visit_prep_interviews as route
import app.services.visit_prep_interviews as interviews
from app.configuration.visit_prep import VisitPrepSettings
from app.routes.middleware import SESSION_SECRET_KEY
from app.server import api_app
from app.services.visit_prep_patient_continuation import (
    COOKIE_NAME,
    open_confirmation,
    seal_confirmation,
)
from app.services.visit_preps import revoke_visit_prep, rotate_visit_prep_credential

from activemodel.session_manager import get_session
from sqlalchemy import DateTime, literal, update
from sqlmodel import SQLModel

from tests.integration.visit_prep_contribution_http_test import _headers
from tests.services.visit_prep_contribution_projection_test import _prepared

PREFIX = "/external/v1/visit-prep"
CONTINUED = {"X-Visit-Prep-Continuation": "1"}


def _client(monkeypatch, redis):
    monkeypatch.setattr(route.rate_limits, "get_rate_limit_redis", lambda: redis)
    return TestClient(
        api_app,
        base_url="https://testserver",
        client=(f"2001:db8::{uuid4().hex[:4]}:{uuid4().hex[:4]}", 50000),
    )


def _confirmed(monkeypatch, redis, *, role="patient", complete=True):
    fixture = _prepared(monkeypatch, "remembered", role=role, complete=complete)
    client = _client(monkeypatch, redis)
    response = client.post(
        PREFIX + "/confirm",
        headers=_headers(fixture) | CONTINUED,
        json={"first_name": "José", "role": role},
    )
    assert response.status_code == 200
    assert response.json()["interview"]["continued_role"] == role
    cookie = response.headers["set-cookie"]
    assert all(
        value in cookie
        for value in ("HttpOnly", "Secure", "SameSite=strict", "Path=/", "expires=")
    )
    assert fixture.access_token not in cookie and "José" not in cookie
    assert "Domain=" not in cookie
    return fixture, client


@pytest.mark.parametrize("role", ["patient", "caregiver"])
def test_one_check_survives_cookie_only_reload_and_full_review_beyond_guess_limit(
    monkeypatch, owned_rate_limit_redis, role
):
    fixture, client = _confirmed(monkeypatch, owned_rate_limit_redis, role=role)
    for headers in (CONTINUED, _headers(fixture) | CONTINUED):
        resumed = client.get(PREFIX, headers=headers)
        assert resumed.status_code == 200
        assert resumed.json()["continued_role"] == role
    # Normal confirmed reads no longer consume the eight-attempt identity bucket.
    for _ in range(10):
        read = client.post(
            PREFIX + "/contribution", headers=CONTINUED, json={"role": role}
        )
        assert read.status_code == 200
        assert read.json()["topics"][-1]["answer"] == "My invented health story."
    changed = client.post(
        PREFIX + "/contribution/response",
        headers=CONTINUED | {"Idempotency-Key": str(uuid4())},
        json={
            "role": role,
            "basis": read.json()["basis"],
            "question_key": "story",
            "response_kind": "answered",
            "answer": "My updated invented story.",
        },
    )
    assert changed.status_code == 200
    reviewed = client.post(
        PREFIX + "/contribution/review",
        headers=CONTINUED | {"Idempotency-Key": str(uuid4())},
        json={"role": role, "basis": changed.json()["basis"]},
    )
    assert reviewed.status_code == 200 and reviewed.json()["current_answers_reviewed"]
    final = client.post(
        PREFIX + "/contribution", headers=CONTINUED, json={"role": role}
    )
    assert final.status_code == 200
    story = final.json()["topics"][-1]
    assert story["answer"] == "My updated invented story."
    assert story["initial_answer"] == "My invented health story."
    assert "no-store" in final.headers["cache-control"]
    assert (
        client.post(
            PREFIX + "/contribution",
            headers=CONTINUED,
            json={"role": "caregiver" if role == "patient" else "patient"},
        ).status_code
        == 404
    )


def test_forwarded_link_without_remembered_confirmation_shows_no_saved_content(
    monkeypatch, owned_rate_limit_redis
):
    fixture, original = _confirmed(monkeypatch, owned_rate_limit_redis)
    stranger = _client(monkeypatch, owned_rate_limit_redis)
    resumed = stranger.get(PREFIX, headers=_headers(fixture) | CONTINUED)
    assert resumed.status_code == 200
    assert resumed.json()["status"] == "confirmation_required"
    assert resumed.json()["continued_role"] is None
    assert resumed.json()["physician_display_name"] is None
    for headers in (_headers(fixture) | CONTINUED, CONTINUED):
        denied = stranger.post(
            PREFIX + "/contribution", headers=headers, json={"role": "patient"}
        )
        assert denied.status_code == 404
        assert "invented health story" not in denied.text
    assert original.get(PREFIX, headers=CONTINUED).status_code == 200


@pytest.mark.parametrize(
    "action", ["rotate", "revoke", "wrong_recipient", "expire", "changed_proof"]
)
def test_current_generation_and_revocation_checks_fence_return_access(
    monkeypatch, owned_rate_limit_redis, action
):
    fixture, client = _confirmed(
        monkeypatch, owned_rate_limit_redis, complete=action != "rotate"
    )
    if action == "rotate":
        rotate_visit_prep_credential(
            owner_practice_member_id=fixture.owner.id,
            owner_doctor_id=fixture.doctor.id,
            practice_id=fixture.practice.id,
            visit_prep_id=fixture.prep_id,
            expected_credential_generation=1,
        )
    elif action == "revoke":
        revoke_visit_prep(
            owner_practice_member_id=fixture.owner.id,
            owner_doctor_id=fixture.doctor.id,
            practice_id=fixture.practice.id,
            visit_prep_id=fixture.prep_id,
            expected_credential_generation=1,
            reason="physician_revoked",
        )
    elif action == "wrong_recipient":
        assert (
            client.post(PREFIX + "/wrong-recipient", headers=CONTINUED).status_code
            == 200
        )
    elif action == "expire":
        # Advance only the selected database-time expression, not immutable rows.
        future = datetime.now(UTC) + timedelta(days=30)
        monkeypatch.setattr(
            interviews.func,
            "clock_timestamp",
            lambda: literal(future, type_=DateTime(timezone=True)),
        )
    else:
        cookie = client.cookies.get(COOKIE_NAME)
        changed = replace(
            open_confirmation(cookie),
            confirmed_at=datetime.now(UTC) - timedelta(seconds=1),
        )
        client.cookies.clear()
        client.cookies.set(COOKIE_NAME, seal_confirmation(changed))
    assert client.get(PREFIX, headers=CONTINUED).status_code == 404
    assert (
        client.post(
            PREFIX + "/contribution", headers=CONTINUED, json={"role": "patient"}
        ).status_code
        == 404
    )


@pytest.mark.parametrize(
    "extra",
    [
        {"Origin": "https://evil.example.test"},
        {"Sec-Fetch-Site": "cross-site"},
        {"Authorization": "Bearer malformed"},
        {"X-Visit-Prep-Continuation": "0"},
    ],
)
def test_cookie_cannot_override_cross_site_or_malformed_request(
    monkeypatch, owned_rate_limit_redis, extra
):
    _, client = _confirmed(monkeypatch, owned_rate_limit_redis)
    response = client.post(
        PREFIX + "/contribution", headers=CONTINUED | extra, json={"role": "patient"}
    )
    assert response.status_code == 404
    assert "invented health story" not in response.text


@pytest.mark.parametrize(
    "gate", ["release", "membership", "baa", "entitlement", "attestation", "doctor"]
)
def test_remembered_confirmation_never_outlives_current_practice_authority(
    monkeypatch, owned_rate_limit_redis, gate
):
    fixture, client = _confirmed(monkeypatch, owned_rate_limit_redis)
    now = datetime.now(UTC)
    if gate == "release":
        monkeypatch.setattr(
            interviews.visit_prep_access, "VISIT_PREP_SETTINGS", VisitPrepSettings()
        )
    else:
        with get_session() as session:
            if gate == "membership":
                table = SQLModel.metadata.tables["practice_member"]
                identifier = fixture.owner.id
                values = dict(
                    access_revoked_at=now,
                    access_revoked_by_ref="synthetic:offboard",
                    access_revocation_reason="Synthetic offboarding",
                    access_records_retain_until=now + timedelta(days=365 * 8),
                )
            elif gate == "attestation":
                table = SQLModel.metadata.tables["practice_member"]
                identifier = fixture.owner.id
                values = dict(attested_at=None, attestation_name=None)
            elif gate == "doctor":
                table = SQLModel.metadata.tables["doctor"]
                identifier = fixture.doctor.id
                values = dict(deleted_at=now)
            else:
                table = SQLModel.metadata.tables["practice"]
                identifier = fixture.practice.id
                values = (
                    dict(negotiated_baa_on_file=False)
                    if gate == "baa"
                    else dict(billing_exempt=False)
                )
            session.execute(
                update(table).where(table.c.id == identifier).values(**values)
            )
            session.commit()
    for headers in (CONTINUED, _headers(fixture) | CONTINUED):
        assert client.get(PREFIX, headers=headers).status_code == 404
        denied = client.post(
            PREFIX + "/contribution", headers=headers, json={"role": "patient"}
        )
        assert denied.status_code == 404 and "invented health story" not in denied.text


def test_cookie_from_another_invitation_does_not_select_the_wrong_preparation(
    monkeypatch, owned_rate_limit_redis
):
    _, client = _confirmed(monkeypatch, owned_rate_limit_redis)
    other = _prepared(monkeypatch, "other-remembered")
    resumed = client.get(PREFIX, headers=_headers(other) | CONTINUED)
    assert (
        resumed.status_code == 200
        and resumed.json()["status"] == "confirmation_required"
    )
    denied = client.post(
        PREFIX + "/contribution",
        headers=_headers(other) | CONTINUED,
        json={"role": "patient"},
    )
    assert denied.status_code == 404 and "invented health story" not in denied.text


@pytest.mark.parametrize("duplicate", ["origin", "continuation", "cookie"])
def test_ambiguous_continuation_headers_are_denied(
    monkeypatch, owned_rate_limit_redis, duplicate
):
    _, client = _confirmed(monkeypatch, owned_rate_limit_redis)
    headers = [("X-Visit-Prep-Continuation", "1")]
    if duplicate == "origin":
        headers += [("Origin", "https://testserver")] * 2
    elif duplicate == "continuation":
        headers += [("X-Visit-Prep-Continuation", "1")]
    else:
        cookie = client.cookies.get(COOKIE_NAME)
        headers += [("Cookie", f"{COOKIE_NAME}={cookie}; {COOKIE_NAME}={cookie}")]
    assert client.get(PREFIX, headers=headers).status_code == 404


def test_remembered_cookie_is_not_usable_in_support_view(
    monkeypatch, owned_rate_limit_redis
):
    import base64
    import json

    _, client = _confirmed(monkeypatch, owned_rate_limit_redis)
    payload = base64.b64encode(
        json.dumps(
            {
                "view_as_session_id": "synthetic-view",
                "view_as_browser_nonce": "synthetic-nonce",
            }
        ).encode()
    )
    session_cookie = TimestampSigner(str(SESSION_SECRET_KEY)).sign(payload).decode()
    confirmation = client.cookies.get(COOKIE_NAME)
    headers = CONTINUED | {
        "Cookie": f"session={session_cookie}; {COOKIE_NAME}={confirmation}"
    }
    assert client.get(PREFIX, headers=headers).status_code == 404
    assert (
        client.post(
            PREFIX + "/contribution", headers=headers, json={"role": "patient"}
        ).status_code
        == 404
    )
