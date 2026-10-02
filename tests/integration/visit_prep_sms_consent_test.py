"""Actual dormant SMS choices over owned PostgreSQL and Redis; no real sends."""

from dataclasses import replace
from uuid import uuid4

import pytest

from app.configuration.visit_prep import load_visit_prep_settings
from app.services import visit_prep_access
from app.services import visit_prep_sms_consent as consent
from app.services.visit_prep_email_contract import VisitPrepEmailInvalid
from app.services.visit_prep_interviews import (
    ResponseInput,
    complete_interview,
    get_interview,
    submit_response,
)
from app.services.visit_prep_patient_continuation import COOKIE_NAME, open_confirmation

from activemodel.session_manager import get_session
from app.models.authority import AuthorityAuditEvent
from app.models.visit_prep_sms_consent import VisitPrepSMSConsentEvent
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlmodel import select

from tests.integration.visit_prep_patient_continuation_http_test import (
    CONTINUED,
    PREFIX,
    _client,
)
from tests.integration.visit_prep_reschedule_continuity_http_test import _move
from tests.services.visit_prep_interviews_test import _fixture, _respond

SMS = PREFIX + "/sms-consent"


def _ready(monkeypatch, redis, *, role="patient", phone="+12025550123"):
    fixture = _fixture(monkeypatch, "sms-choice", recipient_role=role, phone=phone)
    monkeypatch.setattr(
        visit_prep_access,
        "VISIT_PREP_SETTINGS",
        replace(
            visit_prep_access.VISIT_PREP_SETTINGS,
            sms_consent_capture_enabled=True,
            email_consent_capture_enabled=True,
        ),
    )
    client = _client(monkeypatch, redis)
    headers = {"Authorization": f"Bearer {fixture.access_token}"} | CONTINUED
    assert (
        client.post(
            PREFIX + "/confirm",
            headers=headers,
            json={"first_name": "José", "role": role},
        ).status_code
        == 200
    )
    return fixture, client


def _view(client, role="patient"):
    return client.post(SMS, headers=CONTINUED, json={"role": role})


def _payload(view, *, kind="granted"):
    return {
        "role": view["actor_role"],
        "expected_event_sequence": view["event_sequence"],
        "event_kind": kind,
        "disclosure_fingerprint": view[
            "grant_disclosure_fingerprint"
            if kind == "granted"
            else "withdrawal_disclosure_fingerprint"
        ],
        "expires_at": view["maximum_expires_at"] if kind == "granted" else None,
        "recipient_control_attested": kind == "granted",
        "caregiver_authority_attested": kind == "granted"
        and view["actor_role"] == "caregiver",
    }


def _choose(client, payload, key=None):
    return client.post(
        SMS + "/decision",
        headers=CONTINUED | {"Idempotency-Key": key or str(uuid4())},
        json=payload,
    )


def _rows(fixture):
    with get_session() as session:
        return session.exec(
            select(VisitPrepSMSConsentEvent)
            .where(VisitPrepSMSConsentEvent.visit_prep_id == fixture.prep_id)
            .order_by(VisitPrepSMSConsentEvent.event_sequence)
        ).all()


@pytest.mark.parametrize("role", ["patient", "caregiver"])
def test_real_confirmed_sms_grant_withdrawal_old_retry_and_independent_email(
    monkeypatch, owned_rate_limit_redis, role
):
    fixture, client = _ready(monkeypatch, owned_rate_limit_redis, role=role)
    first = _view(client, role)
    assert first.status_code == 200
    view = first.json()
    assert view["contract"] == "visit_prep.patient_sms_consent.v1"
    assert (
        view["contact_available"] and view["can_grant"] and view["event_sequence"] == 0
    )
    assert (
        view["sending_authority"] == "not_qualified"
        and view["delivery_status"] == "not_connected"
    )
    original, key = _payload(view), str(uuid4())
    grant = _choose(client, original, key)
    assert grant.status_code == 200, grant.text
    assert grant.json()["disposition"] == "recorded"
    current = _view(client, role).json()
    assert current["status"] == "grant_recorded" and current["event_sequence"] == 1
    assert _choose(client, _payload(current, kind="withdrawn")).status_code == 200
    replay = _choose(client, original, key)
    assert replay.status_code == 200
    assert (
        replay.json()["event_sequence"] == 1
        and replay.json()["current_event_sequence"] == 2
    )
    assert replay.json()["disposition"] == "replayed"
    assert _choose(client, original).status_code == 409
    assert _view(client, role).json()["status"] == "withdrawn"
    email = client.post(
        PREFIX + "/email-consent", headers=CONTINUED, json={"role": role}
    )
    assert email.status_code == 200 and email.json()["event_sequence"] == 0
    wrong_disclosure = _payload(_view(client, role).json()) | {
        "disclosure_fingerprint": email.json()["grant_disclosure_fingerprint"]
    }
    assert _choose(client, wrong_disclosure).status_code == 409
    rows = _rows(fixture)
    assert len(rows) == 2
    with get_session() as session:
        for row in rows:
            prep = session.get(consent.VisitPrep, fixture.prep_id)
            assert prep is not None
            consent._verify(session, row, prep)
            assert not any(
                value in row.evidence_ciphertext
                for value in (fixture.access_token, "+12025550123", "José")
            )
    for response in (first, grant, replay, email):
        assert (
            response.headers["Cache-Control"] == "no-store"
            and response.headers["Referrer-Policy"] == "no-referrer"
        )
        assert not any(
            value in response.text
            for value in (
                fixture.access_token,
                str(fixture.prep_id),
                "+12025550123",
                "patient@example.com",
                "José",
                "ciphertext",
            )
        )
    assert (
        _respond(fixture, "reason", answer="Invented patient priority.").disposition
        == "accepted"
    )


def test_missing_phone_denies_grant_without_blocking_preparation(
    monkeypatch, owned_rate_limit_redis
):
    fixture, client = _ready(monkeypatch, owned_rate_limit_redis, phone=None)
    view = _view(client).json()
    assert not view["contact_available"] and not view["can_grant"]
    assert _choose(client, _payload(view)).status_code == 409
    assert not _rows(fixture)
    assert (
        _respond(fixture, "reason", answer="Preparation without texts.").disposition
        == "accepted"
    )


def test_reschedule_preserves_choice_and_one_confirmation_without_renewing_grant(
    monkeypatch, owned_rate_limit_redis
):
    fixture, client = _ready(monkeypatch, owned_rate_limit_redis)
    proof = open_confirmation(client.cookies.get(COOKIE_NAME))
    view = _view(client).json()
    original, key = _payload(view), str(uuid4())
    assert _choose(client, original, key).status_code == 200
    before = _rows(fixture)[0]
    _move(fixture, days=1)
    current = _view(client)
    assert current.status_code == 200 and current.json()["status"] == "grant_recorded"
    assert current.json()["expires_at"] == view["maximum_expires_at"]
    assert (
        open_confirmation(client.cookies.get(COOKIE_NAME)).confirmed_at
        == proof.confirmed_at
    )
    assert _choose(client, original, key).status_code == 200
    assert _rows(fixture)[0].evidence_ciphertext == before.evidence_ciphertext
    assert (
        _choose(client, _payload(current.json(), kind="withdrawn")).status_code == 200
    )


def test_completion_blocks_new_grants_but_keeps_withdrawal(
    monkeypatch, owned_rate_limit_redis
):
    fixture, client = _ready(monkeypatch, owned_rate_limit_redis)
    assert _choose(client, _payload(_view(client).json())).status_code == 200
    while (
        question := get_interview(access_token=fixture.access_token).current_question
    ) is not None:
        submit_response(
            access_token=fixture.access_token,
            idempotency_key=str(uuid4()),
            response=ResponseInput(question.key, "skipped"),
        )
    assert (
        complete_interview(access_token=fixture.access_token).disposition == "completed"
    )
    view = _view(client).json()
    assert not view["can_grant"]
    assert _choose(client, _payload(view)).status_code == 409
    assert _choose(client, _payload(view, kind="withdrawn")).status_code == 200


@pytest.mark.parametrize(
    "invalid",
    [
        {"recipient_control_attested": False},
        {"caregiver_authority_attested": True},
        {"expires_at": "2099-01-01T00:00:00Z"},
        {"first_name": "Wrong invented recipient"},
    ],
)
def test_grant_checks_current_disclosure_recipient_attestations_and_deadline(
    monkeypatch, owned_rate_limit_redis, invalid
):
    fixture, client = _ready(monkeypatch, owned_rate_limit_redis)
    response = _choose(client, _payload(_view(client).json()) | invalid)
    assert response.status_code in {404, 409}
    assert not _rows(fixture)


def test_late_capture_gate_failure_rolls_back_choice_and_audit(
    monkeypatch, owned_rate_limit_redis
):
    fixture, client = _ready(monkeypatch, owned_rate_limit_redis)
    payload = _payload(_view(client).json())
    original = consent._checked
    calls = 0
    key = str(uuid4())

    def fail_after_writes(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise VisitPrepEmailInvalid()
        return original(*args, **kwargs)

    monkeypatch.setattr(consent, "_checked", fail_after_writes)
    assert _choose(client, payload, key).status_code == 409
    assert not _rows(fixture)
    with get_session() as session:
        assert not session.exec(
            select(AuthorityAuditEvent).where(
                AuthorityAuditEvent.resource_type == "visit_prep_sms_consent",
                AuthorityAuditEvent.client_event_id == key,
            )
        ).all()


def test_sql_guard_prevents_duplicate_sequence_and_mutations(
    monkeypatch, owned_rate_limit_redis
):
    fixture, client = _ready(monkeypatch, owned_rate_limit_redis)
    assert _choose(client, _payload(_view(client).json())).status_code == 200
    row = _rows(fixture)[0]
    with get_session() as session:
        for query in (
            "UPDATE public.visit_prep_sms_consent_event SET event_kind='withdrawn' WHERE id=:id",
            "DELETE FROM public.visit_prep_sms_consent_event WHERE id=:id",
            "TRUNCATE public.visit_prep_sms_consent_event",
        ):
            with pytest.raises(DBAPIError, match="append-only|cannot be truncated"):
                session.execute(text(query), {"id": row.id.uuid})
            session.rollback()
        values = row.model_dump()
        values.pop("id")
        values.update(
            evidence_ciphertext=row.evidence_ciphertext,
            recipient_digest=row.recipient_digest,
            payload_hmac_sha256=row.payload_hmac_sha256,
            operation_id=uuid4(),
        )
        duplicate = VisitPrepSMSConsentEvent(**values)
        session.add(duplicate)
        with pytest.raises(DBAPIError, match="sequence or deadline"):
            session.flush()
        session.rollback()


def test_capture_controls_are_independent_and_fail_closed(
    monkeypatch, owned_rate_limit_redis
):
    fixture, client = _ready(monkeypatch, owned_rate_limit_redis)
    monkeypatch.setattr(
        visit_prep_access,
        "VISIT_PREP_SETTINGS",
        replace(
            visit_prep_access.VISIT_PREP_SETTINGS, sms_consent_capture_enabled=False
        ),
    )
    assert _view(client).status_code == 409
    assert client.get(PREFIX, headers=CONTINUED).status_code == 200
    assert load_visit_prep_settings({}).sms_consent_capture_enabled is False
    settings = load_visit_prep_settings(
        {"VISIT_PREP_SMS_CONSENT_CAPTURE_ENABLED": "true"}
    )
    assert settings.sms_consent_capture_enabled and not settings.sms_send_enabled
    assert not load_visit_prep_settings(
        {
            "FF_VISIT_PREP_202608": "true",
            "VISIT_PREP_SMS_CONSENT_CAPTURE_ENABLED": "invalid",
        }
    ).configuration_valid


@pytest.mark.parametrize("suffix", ["", "/decision"])
def test_sms_routes_keep_private_origin_body_and_validation_boundary(
    monkeypatch, owned_rate_limit_redis, suffix
):
    fixture, client = _ready(monkeypatch, owned_rate_limit_redis)
    headers = CONTINUED | {"Idempotency-Key": str(uuid4())}
    payload = {"role": "patient"} if not suffix else _payload(_view(client).json())
    attempts = [
        client.post(SMS + suffix + "?token=private", headers=headers, json=payload),
        client.post(
            SMS + suffix,
            headers=headers | {"Origin": "https://untrusted.example.invalid"},
            json=payload,
        ),
        client.post(
            SMS + suffix, headers=headers, json=payload | {"unknown": "private-value"}
        ),
        client.get(SMS + suffix, headers=headers),
    ]
    assert all(response.status_code in {404, 422} for response in attempts)
    for response in attempts:
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["referrer-policy"] == "no-referrer"
        assert not any(
            value in response.text
            for value in (
                fixture.access_token,
                "private-value",
                str(fixture.prep_id),
                "+12025550123",
            )
        )
    assert not _rows(fixture)
