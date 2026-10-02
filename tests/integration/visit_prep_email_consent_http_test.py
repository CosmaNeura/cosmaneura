"""Real public consent API, with invented data and owned PostgreSQL/Redis only."""

import base64
import json
from dataclasses import replace
from itertools import count
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from itsdangerous import TimestampSigner

import app.routes.visit_prep_interviews as route
from app.configuration.visit_prep import VisitPrepSettings
from app.routes.middleware import SESSION_SECRET_KEY, allowed_hosts
from app.server import api_app
from app.services import visit_prep_access
from app.services.visit_prep_interviews import report_wrong_recipient
from app.services.visit_preps import rotate_visit_prep_credential

from app.models.authority import AuthorityAuditEvent
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session

from tests.services.visit_prep_email_consent_test import (
    _audits,
    _capture,
    _decision,
    _rows,
)
from tests.services.visit_prep_email_consent_view_test import (
    _public_capture,
    _read_audits,
    _ready,
)

PREFIX = "/external/v1/visit-prep/email-consent"
_PEERS = count(1)


def _client(monkeypatch, owned_rate_limit_redis):
    monkeypatch.setattr(
        route.rate_limits, "get_rate_limit_redis", lambda: owned_rate_limit_redis
    )
    return TestClient(
        api_app,
        base_url=allowed_hosts(with_scheme=True)[0],
        # Distinct from interview/contribution/browser suite peers. Preserve
        # real counters throughout each journey, never reset or weaken them.
        client=(f"192.0.2.{next(_PEERS)}", 50000),
    )


def _headers(fixture, *, key=None):
    return {
        "Authorization": f"Bearer {fixture.access_token}",
        **({"Idempotency-Key": key} if key else {}),
    }


def _confirm(role="patient"):
    return dict(first_name="José", role=role)


def _payload(fixture, *, role="patient", kind="granted", sequence=0):
    choice = _decision(fixture, role=role, kind=kind, sequence=sequence)
    return _confirm(role) | dict(
        expected_event_sequence=choice.expected_event_sequence,
        event_kind=choice.event_kind,
        disclosure_fingerprint=choice.disclosure_fingerprint,
        expires_at=choice.expires_at.isoformat() if choice.expires_at else None,
        recipient_control_attested=choice.recipient_control_attested,
        caregiver_authority_attested=choice.caregiver_authority_attested,
    )


def _private(response, fixture):
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["Referrer-Policy"] == "no-referrer"
    for private in (
        fixture.access_token,
        str(fixture.prep_id),
        str(fixture.owner.id),
        "patient@example.com",
        "José",
        "ciphertext",
        "source_event_id",
        "actor_binding_digest",
    ):
        assert private not in response.text


@pytest.mark.parametrize("role", ["patient", "caregiver"])
def test_real_optional_choice_read_grant_withdrawal_and_old_replay(
    monkeypatch, owned_rate_limit_redis, role
):
    fixture = _ready(monkeypatch, "consent-http-choice", role=role)
    client = _client(monkeypatch, owned_rate_limit_redis)
    initial = client.post(PREFIX, headers=_headers(fixture), json=_confirm(role))
    assert initial.status_code == 200
    view = initial.json()
    assert view["contract"] == "visit_prep.patient_email_consent.v1"
    assert view["status"] == "not_recorded" and view["event_sequence"] == 0
    assert view["can_grant"] and view["actor_role"] == role
    assert view["identity_assurance"] == "invitation_bearer"
    assert view["sending_authority"] == "not_qualified"
    assert view["delivery_status"] == "not_connected"
    assert not _rows(fixture)
    intent = _payload(fixture, role=role)
    key = str(uuid4())
    grant = client.post(
        PREFIX + "/decision", headers=_headers(fixture, key=key), json=intent
    )
    assert grant.status_code == 200 and grant.json()["disposition"] == "recorded"
    assert grant.json()["event_sequence"] == grant.json()["current_event_sequence"] == 1
    reread = client.post(PREFIX, headers=_headers(fixture), json=_confirm(role))
    assert reread.status_code == 200 and reread.json()["status"] == "grant_recorded"
    withdrawn = client.post(
        PREFIX + "/decision",
        headers=_headers(fixture, key=str(uuid4())),
        json=_payload(fixture, role=role, kind="withdrawn", sequence=1),
    )
    assert (
        withdrawn.status_code == 200 and withdrawn.json()["event_kind"] == "withdrawn"
    )
    replayed = client.post(
        PREFIX + "/decision", headers=_headers(fixture, key=key), json=intent
    )
    assert replayed.status_code == 200 and replayed.json()["disposition"] == "replayed"
    assert (
        replayed.json()["event_sequence"] == 1
        and replayed.json()["current_event_sequence"] == 2
    )
    stale = client.post(
        PREFIX + "/decision", headers=_headers(fixture, key=str(uuid4())), json=intent
    )
    assert stale.status_code == 409
    final = client.post(PREFIX, headers=_headers(fixture), json=_confirm(role))
    assert final.status_code == 200 and final.json()["status"] == "withdrawn"
    assert final.json()["event_sequence"] == 2 and final.json()["expires_at"] is None
    assert len(_rows(fixture)) == len(_audits(fixture)) == 2
    for response in (initial, grant, reread, withdrawn, replayed, stale, final):
        _private(response, fixture)


@pytest.mark.parametrize("suffix", ["", "/decision"])
@pytest.mark.parametrize(
    "state",
    [
        "wrong_name",
        "wrong_role",
        "unconfirmed",
        "wrong_recipient",
        "rotation",
        "baa",
        "control_off",
        "public_off",
    ],
)
def test_current_confirmation_authority_and_disabled_controls_never_disclose(
    monkeypatch, owned_rate_limit_redis, suffix, state
):
    from tests.services.visit_prep_interviews_test import _fixture

    fixture = (
        _fixture(monkeypatch, "consent-http-unconfirmed")
        if state == "unconfirmed"
        else _ready(monkeypatch, "consent-http-denial")
    )
    if state == "unconfirmed":
        monkeypatch.setattr(
            visit_prep_access,
            "VISIT_PREP_SETTINGS",
            replace(
                visit_prep_access.VISIT_PREP_SETTINGS,
                email_consent_capture_enabled=True,
            ),
        )
    if state == "wrong_recipient":
        report_wrong_recipient(access_token=fixture.access_token)
    elif state == "rotation":
        rotate_visit_prep_credential(
            owner_practice_member_id=fixture.owner.id,
            owner_doctor_id=fixture.doctor.id,
            practice_id=fixture.practice.id,
            visit_prep_id=fixture.prep_id,
            expected_credential_generation=1,
        )
    elif state == "baa":
        fixture.practice.negotiated_baa_on_file = False
        fixture.practice.save()
    elif state == "control_off":
        monkeypatch.setattr(
            visit_prep_access,
            "VISIT_PREP_SETTINGS",
            replace(
                visit_prep_access.VISIT_PREP_SETTINGS,
                email_consent_capture_enabled=False,
            ),
        )
    elif state == "public_off":
        monkeypatch.setattr(
            visit_prep_access, "VISIT_PREP_SETTINGS", VisitPrepSettings()
        )
    payload = _payload(fixture) if suffix else _confirm()
    if state == "wrong_name":
        payload["first_name"] = "Wrong synthetic recipient"
    elif state == "wrong_role":
        payload = (
            _payload(fixture, role="caregiver") if suffix else _confirm("caregiver")
        )
    client = _client(monkeypatch, owned_rate_limit_redis)
    response = client.post(
        PREFIX + suffix, headers=_headers(fixture, key=str(uuid4())), json=payload
    )
    assert response.status_code == (404 if state == "public_off" else 409)
    _private(response, fixture)
    assert not _rows(fixture) and not _read_audits(fixture)


@pytest.mark.parametrize(
    "changes",
    [
        {"expires_at": 1790999999},
        {"expires_at": "2026-10-03T12:00:00"},
        {"expires_at": None},
        {"expires_at": "2020-10-03T12:00:00Z"},
        {"recipient_control_attested": 1},
        {"recipient_control_attested": False},
        {"caregiver_authority_attested": True},
        {"expected_event_sequence": True},
        {"expected_event_sequence": "0"},
        {"expected_event_sequence": 2_147_483_647},
        {"disclosure_fingerprint": "A" * 64},
        {"disclosure_fingerprint": "a" * 64},
        {"owner_practice_member_id": "private-override"},
        {"first_name": None},
    ],
)
def test_decision_shape_is_strict_and_never_echoes_private_validation(
    monkeypatch, owned_rate_limit_redis, changes
):
    fixture = _ready(monkeypatch, "consent-http-strict")
    client = _client(monkeypatch, owned_rate_limit_redis)
    response = client.post(
        PREFIX + "/decision",
        headers=_headers(fixture, key=str(uuid4())),
        json=_payload(fixture) | changes,
    )
    assert response.status_code in {409, 422}
    assert set(response.json()) == {"detail"}
    _private(response, fixture)
    assert not _rows(fixture)


@pytest.mark.parametrize("suffix", ["", "/decision"])
def test_support_view_bounded_body_queries_and_get_are_private(
    monkeypatch, owned_rate_limit_redis, suffix
):
    fixture = _ready(monkeypatch, "consent-http-privacy")
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
    payload = _payload(fixture) if suffix else _confirm()
    support = client.post(
        PREFIX + suffix,
        headers=_headers(fixture, key=str(uuid4())) | {"Cookie": f"session={cookie}"},
        json=payload,
    )
    get = client.get(PREFIX + suffix, headers=_headers(fixture))
    query = client.post(
        PREFIX + suffix + "?access_token=private_query",
        headers=_headers(fixture, key=str(uuid4())),
        json=payload,
    )
    too_large = client.post(
        PREFIX + suffix,
        headers=_headers(fixture, key=str(uuid4())),
        content=json.dumps(payload | {"private": "private_body" * 1100}),
    )
    assert support.status_code == get.status_code == 404
    assert query.status_code == 422 and too_large.status_code == 413
    for response in (support, get, query, too_large):
        _private(response, fixture)
        assert (
            "private_query" not in response.text and "private_body" not in response.text
        )
    assert not _rows(fixture) and not _read_audits(fixture)


@pytest.mark.parametrize("suffix", ["", "/decision"])
def test_read_and_decision_share_existing_confirmation_attempt_budget(
    monkeypatch, owned_rate_limit_redis, suffix
):
    fixture = _ready(monkeypatch, "consent-http-rate")
    client = _client(monkeypatch, owned_rate_limit_redis)
    payload = _payload(fixture) if suffix else _confirm()
    for _ in range(route.rate_limits.CONFIRMATION_LIMIT):
        denied = client.post(
            PREFIX + suffix,
            headers=_headers(fixture, key=str(uuid4())),
            json=payload | {"first_name": "Wrong synthetic name"},
        )
        assert denied.status_code == 409
    limited = client.post(
        PREFIX + suffix, headers=_headers(fixture, key=str(uuid4())), json=payload
    )
    assert limited.status_code == 429
    assert limited.headers["Retry-After"] == str(route.rate_limits.WINDOW_SECONDS)
    _private(limited, fixture)
    assert not _rows(fixture)


@pytest.mark.parametrize(
    "key",
    [
        None,
        "invalid",
        "AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA",
        "00000000-0000-0000-0000-000000000000",
        "00000000-0000-4000-8000-000000000001",
    ],
)
def test_decision_keys_are_required_canonical_and_single(
    monkeypatch, owned_rate_limit_redis, key
):
    fixture = _ready(monkeypatch, "consent-http-keys")
    client = _client(monkeypatch, owned_rate_limit_redis)
    headers = _headers(fixture, key=key)
    if key == "00000000-0000-4000-8000-000000000001":
        headers = list(headers.items()) + [("Idempotency-Key", str(uuid4()))]
    response = client.post(
        PREFIX + "/decision", headers=headers, json=_payload(fixture)
    )
    assert response.status_code in {409, 422}
    _private(response, fixture)
    assert not _rows(fixture)


@pytest.mark.parametrize("failure", ["audit", "lost_ack"])
def test_actual_atomic_capture_failure_and_lost_commit_ack_recover_via_read(
    monkeypatch, owned_rate_limit_redis, failure
):
    fixture = _ready(monkeypatch, "consent-http-recovery")
    client = _client(monkeypatch, owned_rate_limit_redis)
    original_flush, original_commit = Session.flush, Session.commit

    def fail_audit(session, *args, **kwargs):
        if any(isinstance(row, AuthorityAuditEvent) for row in session.new):
            raise SQLAlchemyError("synthetic private audit diagnostic")
        return original_flush(session, *args, **kwargs)

    def lost_ack(session):
        original_commit(session)
        raise SQLAlchemyError("synthetic private commit diagnostic")

    with monkeypatch.context() as patch:
        patch.setattr(
            Session,
            "flush" if failure == "audit" else "commit",
            fail_audit if failure == "audit" else lost_ack,
        )
        response = client.post(
            PREFIX + "/decision",
            headers=_headers(fixture, key=str(uuid4())),
            json=_payload(fixture),
        )
    assert response.status_code == 409 and "diagnostic" not in response.text
    _private(response, fixture)
    recovered = client.post(PREFIX, headers=_headers(fixture), json=_confirm())
    assert recovered.status_code == 200
    assert recovered.json()["status"] == (
        "not_recorded" if failure == "audit" else "grant_recorded"
    )
    assert (
        len(_rows(fixture)) == len(_audits(fixture)) == (0 if failure == "audit" else 1)
    )


def test_new_read_recovers_latest_external_withdrawal_and_has_closed_openapi(
    monkeypatch, owned_rate_limit_redis
):
    fixture = _ready(monkeypatch, "consent-http-contract")
    _public_capture(fixture, _decision(fixture))
    _capture(fixture, _decision(fixture, kind="withdrawn", sequence=1))
    client = _client(monkeypatch, owned_rate_limit_redis)
    current = client.post(PREFIX, headers=_headers(fixture), json=_confirm())
    assert current.status_code == 200 and current.json()["status"] == "withdrawn"
    schema = api_app.openapi()
    for path in (PREFIX, PREFIX + "/decision"):
        assert set(schema["paths"][path]) == {"post"}
        operation = schema["paths"][path]["post"]
        assert operation["security"] == [{"VisitPrepBearer": []}]
        assert operation["requestBody"]["required"]
    component = schema["components"]["schemas"][
        "VisitPrepPatientEmailConsentDecisionRequest"
    ]
    assert component["additionalProperties"] is False
    assert {
        "first_name",
        "role",
        "expected_event_sequence",
        "expires_at",
        "recipient_control_attested",
        "caregiver_authority_attested",
    } <= set(component["required"])
