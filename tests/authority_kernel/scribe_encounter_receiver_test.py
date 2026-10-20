"""Normal human auth, closed input and support-view separation at HTTP boundary."""

import json
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.configuration.scribe_encounters import ScribeEncounterSettings
from app.routes import scribe_encounters as route
from app.routes.embed_context import (
    VerifiedClerkIdentity,
    authenticate_embed_platform_session,
)
from app.services.scribe_encounter_contract import REQUEST_ADAPTER

ACTOR = "authority_actor_00000000000000000000000001"
TENANT = "authority_tenant_00000000000000000000000002"


def body(**changes):
    return (
        dict(
            action="create",
            expected_actor_ref=ACTOR,
            expected_tenant_ref=TENANT,
            request_nonce=str(uuid4()),
            creation_key=str(uuid4()),
            appointment_at="2026-09-29T13:30:00-04:00",
            patient={
                "kind": "new",
                "name": "Invented Patient",
                "date_of_birth": "1990-01-01",
            },
        )
        | changes
    )


def client(*, signed_in=True, enabled=True):
    app = FastAPI()
    route.install(app)
    if signed_in:
        app.dependency_overrides[authenticate_embed_platform_session] = (
            lambda: VerifiedClerkIdentity(
                issuer="https://test.clerk.invalid",
                subject="user_test_provider",
                clerk_session_id="session_test",
            )
        )
    app.dependency_overrides[route.encounter_settings] = (
        lambda: ScribeEncounterSettings(enabled=enabled)
    )
    return TestClient(app)


def headers(value):
    return {
        "Authorization": "Bearer test",
        "Content-Type": "application/json",
        "X-Cosmaneura-Scribe-Encounter-Version": "1",
        "Idempotency-Key": value["creation_key"],
    }


@pytest.mark.parametrize(
    "signed_in,enabled,status", [(False, True, 401), (True, False, 503)]
)
def test_requires_human_sign_in_and_rollout(signed_in, enabled, status):
    value = body()
    result = client(signed_in=signed_in, enabled=enabled).post(
        route.PATH, json=value, headers=headers(value)
    )
    assert result.status_code == status
    assert "Invented Patient" not in result.text
    assert result.headers["cache-control"] == "no-store"


def test_support_view_cannot_submit(monkeypatch):
    monkeypatch.setattr(route, "view_as_browser_session_present", lambda _: True)
    value = body()
    result = client().post(route.PATH, json=value, headers=headers(value))
    assert result.status_code == 403


@pytest.mark.parametrize(
    "change",
    [
        {"actor_id": ACTOR},
        {"consent": True},
        {"appointment_at": "2026-09-29T13:30:00"},
        {"patient": {"kind": "existing", "name": "Invented Patient"}},
        {"patient": {"kind": "new", "name": "  ", "date_of_birth": "1990-01-01"}},
    ],
)
def test_rejects_authority_claims_consent_and_ambiguous_patient_input(change):
    with pytest.raises(ValueError):
        REQUEST_ADAPTER.validate_python(body(**change))


@pytest.mark.parametrize("invalid", ["duplicate", "oversize", "mismatched_key"])
def test_closed_body_and_exact_retry_key(invalid):
    value = body()
    raw = json.dumps(value)
    h = headers(value)
    if invalid == "duplicate":
        raw = raw[:-1] + ',"action":"list"}'
    elif invalid == "oversize":
        raw = " " * 4097
    else:
        h["Idempotency-Key"] = str(uuid4())
    result = client().post(route.PATH, content=raw, headers=h)
    assert result.status_code == 422


def test_delegates_verified_identity_not_submitted_account(monkeypatch):
    captured = {}

    def workflow(**kw):
        captured.update(kw)
        return {
            "version": "1",
            "action": "create",
            "request_nonce": str(kw["request"].request_nonce),
        }

    monkeypatch.setattr(route.scribe_encounters, "encounter_workflow", workflow)
    value = body()
    result = client().post(route.PATH, json=value, headers=headers(value))
    assert result.status_code == 200
    assert (
        captured["subject"] == "user_test_provider"
        and captured["session_id"] == "session_test"
    )
    assert result.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("birth_date", [{}, {"date_of_birth": None}])
def test_unknown_birth_date_is_accepted_and_normalized_at_human_boundary(
    monkeypatch, birth_date
):
    captured = []

    def workflow(**kwargs):
        captured.append(kwargs["request"].patient.date_of_birth)
        return {"version": "1", "action": "create"}

    monkeypatch.setattr(route.scribe_encounters, "encounter_workflow", workflow)
    value = body(
        patient={"kind": "new", "name": "Invented unknown birthday", **birth_date}
    )
    result = client().post(route.PATH, json=value, headers=headers(value))
    assert result.status_code == 200 and captured == [None]
    assert result.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("birth_date", ["", "not-a-date", "2026-02-30"])
def test_optional_birth_date_does_not_accept_invalid_supplied_date(birth_date):
    with pytest.raises(ValueError):
        REQUEST_ADAPTER.validate_python(
            body(
                patient={
                    "kind": "new",
                    "name": "Invented patient",
                    "date_of_birth": birth_date,
                }
            )
        )


def test_existing_non_null_request_retains_exact_canonical_payload():
    value = body()
    value["title"] = "Visit"
    assert REQUEST_ADAPTER.validate_python(value).model_dump(mode="json") == value


def test_private_note_unicode_body_and_exact_key_are_bounded(monkeypatch):
    value = dict(
        action="private_note_save",
        expected_actor_ref=ACTOR,
        expected_tenant_ref=TENANT,
        request_nonce=str(uuid4()),
        appointment_ref="appointment_00000000000000000000000001",
        change_key=str(uuid4()),
        expected_version=0,
        text="é" * 2000,
    )
    h = headers({"creation_key": value["change_key"]})
    monkeypatch.setattr(
        route.scribe_encounters,
        "encounter_workflow",
        lambda **_: {
            "version": "1",
            "private_note": {"text": "", "version": 1, "updated_at": None},
        },
    )
    assert client().post(route.PATH, json=value, headers=h).status_code == 200
    h["Idempotency-Key"] = str(uuid4())
    denied = client().post(route.PATH, json=value, headers=h)
    assert denied.status_code == 422 and "é" not in denied.text


@pytest.mark.parametrize(
    "field,value",
    [
        ("text", "x" * 2001),
        ("text", "\ud800"),
        ("expected_version", True),
        ("clinical_note", "Invented secret"),
    ],
    ids=["oversize", "invalid_unicode", "boolean_version", "clinical_field"],
)
def test_private_note_contract_never_accepts_clinical_or_invalid_version_fields(
    field, value
):
    payload = dict(
        action="private_note_save",
        expected_actor_ref=ACTOR,
        expected_tenant_ref=TENANT,
        request_nonce=str(uuid4()),
        appointment_ref="appointment_00000000000000000000000001",
        change_key=str(uuid4()),
        expected_version=0,
        text="Invented reminder",
    )
    with pytest.raises(ValueError):
        REQUEST_ADAPTER.validate_python(payload | {field: value})
