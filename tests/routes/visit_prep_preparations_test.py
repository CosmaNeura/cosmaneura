from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi import FastAPI, HTTPException, status
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from typeid import TypeID

import app.routes.visit_prep_preparations as route
from app.routes.dependencies.practice_gate import EXEMPT_ROUTE_NAMES
from app.routes.dependencies.visit_prep import require_visit_prep_write
from app.routes.internal import internal_api_app
from app.services.visit_prep_access import VisitPrepAccessDecision
from app.services.visit_preps import (
    MutationDisposition,
    RecipientInput,
    VisitPrepConflict,
    VisitPrepMutationResult,
    VisitPrepNotFound,
    VisitPrepResult,
    VisitPrepUnavailable,
    VisitPrepValidation,
)

from activemodel.types import TypeIDType
from sqlalchemy.exc import SQLAlchemyError

OWNER_ID = cast(TypeIDType, TypeID("practice_member"))
DOCTOR_ID = cast(TypeIDType, TypeID("doctor"))
PRACTICE_ID = cast(TypeIDType, TypeID("practice"))
PREP_ID = cast(TypeIDType, TypeID("visit_prep"))
VERSION_ID = cast(TypeIDType, TypeID("visit_prep_question_set_version"))
PATIENT_ID = cast(TypeIDType, TypeID("patient"))
APPOINTMENT_ID = cast(TypeIDType, TypeID("appointment"))
NOW = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)
SCHEDULED_AT = datetime(2026, 9, 25, 14, 30, tzinfo=UTC)
EXPIRES_AT = datetime(2026, 9, 28, 14, 30, tzinfo=UTC)
IDEMPOTENCY_KEY = "123e4567-e89b-12d3-a456-426614174000"
ACCESS_TOKEN = "vp1_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"


def _decision(*, view_as_active: bool = False) -> VisitPrepAccessDecision:
    return VisitPrepAccessDecision(
        eligible=True,
        can_manage=not view_as_active,
        can_send_email=False,
        can_send_sms=False,
        doctor_id=DOCTOR_ID,
        practice_id=PRACTICE_ID,
        practice_member_id=OWNER_ID,
        denial_reason=None,
        view_as_active=view_as_active,
    )


def _result() -> VisitPrepResult:
    return VisitPrepResult(
        id=PREP_ID,
        question_set_version_id=VERSION_ID,
        patient_id=PATIENT_ID,
        appointment_id=APPOINTMENT_ID,
        lifecycle_state="invited",
        has_email_contact=True,
        has_sms_contact=False,
        physician_display_name="Dr. Example",
        practice_display_name="Example Practice",
        scheduled_at=SCHEDULED_AT,
        timezone_name="UTC",
        schedule_generation=1,
        credential_generation=1,
        credential_expires_at=EXPIRES_AT,
        credential_active=True,
        revoked_at=None,
        created_at=NOW,
        updated_at=NOW,
    )


def _mutation(
    disposition: MutationDisposition = "created",
    *,
    access_token: str | None = ACCESS_TOKEN,
) -> VisitPrepMutationResult:
    return VisitPrepMutationResult(
        visit_prep=_result(),
        disposition=disposition,
        access_token=access_token,
    )


def _create_payload() -> dict[str, Any]:
    return {
        "recipient": {
            "first_name": "Ada",
            "role": "patient",
            "email": "ada@example.test",
        },
        "physician_display_name": "Dr. Example",
        "scheduled_at": SCHEDULED_AT.isoformat(),
        "timezone_name": "UTC",
        "question_set_version_id": str(VERSION_ID),
        "patient_id": str(PATIENT_ID),
        "appointment_id": str(APPOINTMENT_ID),
    }


def _client(decision: VisitPrepAccessDecision | None = None) -> TestClient:
    app = FastAPI()
    app.include_router(
        route.visit_prep_preparations_api_app,
        prefix="/internal/v1",
    )
    app.dependency_overrides[require_visit_prep_write] = lambda: (
        decision or _decision()
    )
    return TestClient(app)


def test_router_is_wired_only_under_the_internal_provider_api():
    api_routes = {
        item.name: item
        for item in internal_api_app.routes
        if isinstance(item, APIRoute)
    }
    expected = {
        "visit_prep_create": ("/internal/v1/visit-prep/preps", "POST"),
        "visit_prep_copy_link": (
            "/internal/v1/visit-prep/preps/{prep_id}/copy-link",
            "POST",
        ),
        "visit_prep_revoke": (
            "/internal/v1/visit-prep/preps/{prep_id}/revoke",
            "POST",
        ),
        "visit_prep_reschedule": (
            "/internal/v1/visit-prep/preps/{prep_id}/reschedule",
            "POST",
        ),
        "visit_prep_reconcile": (
            "/internal/v1/visit-prep/preps/{prep_id}/canonical-links",
            "PUT",
        ),
    }

    for name, (path, method) in expected.items():
        assert api_routes[name].path == path
        assert method in api_routes[name].methods
        assert not api_routes[name].path.startswith("/visit-prep")
    assert expected.keys() <= EXEMPT_ROUTE_NAMES


def test_commands_delegate_only_the_authenticated_owner_scope(monkeypatch):
    calls: list[tuple[str, dict[str, object]]] = []

    def record(
        operation: str,
        disposition: MutationDisposition,
        *,
        access_token: str | None = None,
    ):
        def invoke(**kwargs):
            calls.append((operation, kwargs))
            return _mutation(disposition, access_token=access_token)

        return invoke

    monkeypatch.setattr(
        route.visit_prep_service,
        "create_visit_prep",
        record("create", "created", access_token=ACCESS_TOKEN),
    )
    monkeypatch.setattr(
        route.visit_prep_service,
        "rotate_visit_prep_credential",
        record("copy", "rotated", access_token=ACCESS_TOKEN),
    )
    monkeypatch.setattr(
        route.visit_prep_service,
        "revoke_visit_prep",
        record("revoke", "revoked"),
    )
    monkeypatch.setattr(
        route.visit_prep_service,
        "reschedule_visit_prep",
        record("reschedule", "rescheduled", access_token=ACCESS_TOKEN),
    )
    monkeypatch.setattr(
        route.visit_prep_service,
        "reconcile_visit_prep",
        record("reconcile", "reconciled"),
    )

    with _client() as client:
        responses = [
            client.post(
                "/internal/v1/visit-prep/preps",
                headers={"Idempotency-Key": IDEMPOTENCY_KEY},
                json=_create_payload(),
            ),
            client.post(
                f"/internal/v1/visit-prep/preps/{PREP_ID}/copy-link",
                json={"expected_credential_generation": 1},
            ),
            client.post(
                f"/internal/v1/visit-prep/preps/{PREP_ID}/revoke",
                json={
                    "expected_credential_generation": 1,
                    "reason": "physician_revoked",
                },
            ),
            client.post(
                f"/internal/v1/visit-prep/preps/{PREP_ID}/reschedule",
                json={
                    "expected_credential_generation": 1,
                    "expected_schedule_generation": 1,
                    "scheduled_at": SCHEDULED_AT.isoformat(),
                    "timezone_name": "UTC",
                },
            ),
            client.put(
                f"/internal/v1/visit-prep/preps/{PREP_ID}/canonical-links",
                json={
                    "patient_id": str(PATIENT_ID),
                    "appointment_id": str(APPOINTMENT_ID),
                },
            ),
        ]

    assert [response.status_code for response in responses] == [201, 200, 200, 200, 200]
    assert [operation for operation, _kwargs in calls] == [
        "create",
        "copy",
        "revoke",
        "reschedule",
        "reconcile",
    ]
    for _operation, kwargs in calls:
        assert kwargs["owner_practice_member_id"] == OWNER_ID
        assert kwargs["owner_doctor_id"] == DOCTOR_ID
        assert kwargs["practice_id"] == PRACTICE_ID

    create = calls[0][1]
    assert create["idempotency_key"] == IDEMPOTENCY_KEY
    assert create["recipient"] == RecipientInput(
        first_name="Ada",
        role="patient",
        email="ada@example.test",
    )
    assert create["question_set_version_id"] == VERSION_ID
    assert create["patient_id"] == PATIENT_ID
    assert create["appointment_id"] == APPOINTMENT_ID
    for _operation, kwargs in calls[1:]:
        assert kwargs["visit_prep_id"] == PREP_ID


def test_create_returns_one_time_fragment_url_and_private_headers(monkeypatch):
    monkeypatch.setattr(route, "INTAKE_CHAT_DOMAIN", "patient.example.test")
    monkeypatch.setattr(
        route.visit_prep_service,
        "create_visit_prep",
        lambda **_kwargs: _mutation("created", access_token=ACCESS_TOKEN),
    )

    with _client() as client:
        response = client.post(
            "/internal/v1/visit-prep/preps",
            headers={"Idempotency-Key": IDEMPOTENCY_KEY},
            json=_create_payload(),
        )

    assert response.status_code == status.HTTP_201_CREATED
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.json()["disposition"] == "created"
    access_url = response.json()["access_url"]
    parsed = urlsplit(access_url)
    assert (parsed.scheme, parsed.netloc, parsed.path, parsed.query) == (
        "https",
        "patient.example.test",
        "/visit-prep",
        "",
    )
    assert parse_qs(parsed.fragment) == {"access_token": [ACCESS_TOKEN]}
    assert ACCESS_TOKEN not in parsed.path
    assert ACCESS_TOKEN not in parsed.query


def test_create_replay_returns_200_without_recovering_the_bearer(monkeypatch):
    monkeypatch.setattr(
        route.visit_prep_service,
        "create_visit_prep",
        lambda **_kwargs: _mutation("replayed", access_token=None),
    )

    with _client() as client:
        response = client.post(
            "/internal/v1/visit-prep/preps",
            headers={"Idempotency-Key": IDEMPOTENCY_KEY},
            json=_create_payload(),
        )

    assert response.status_code == status.HTTP_200_OK
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.json()["disposition"] == "replayed"
    assert response.json()["access_url"] is None


@pytest.mark.parametrize(
    ("method", "path", "json", "expected_status"),
    [
        (
            "POST",
            "/internal/v1/visit-prep/preps",
            {
                **_create_payload(),
                "question_set_version_id": "private-malformed-version-id",
            },
            status.HTTP_422_UNPROCESSABLE_ENTITY,
        ),
        (
            "POST",
            "/internal/v1/visit-prep/preps/private-malformed-prep-id/copy-link",
            {"expected_credential_generation": 1},
            status.HTTP_404_NOT_FOUND,
        ),
        (
            "PUT",
            f"/internal/v1/visit-prep/preps/{PREP_ID}/canonical-links",
            {"patient_id": "private-malformed-patient-id"},
            status.HTTP_422_UNPROCESSABLE_ENTITY,
        ),
    ],
)
def test_malformed_identifiers_are_redacted(
    monkeypatch,
    method: str,
    path: str,
    json: dict[str, Any],
    expected_status: int,
):
    def unexpected(**_kwargs):
        raise AssertionError("malformed identifiers must not reach the service")

    monkeypatch.setattr(route.visit_prep_service, "create_visit_prep", unexpected)
    monkeypatch.setattr(
        route.visit_prep_service,
        "rotate_visit_prep_credential",
        unexpected,
    )
    monkeypatch.setattr(
        route.visit_prep_service,
        "reconcile_visit_prep",
        unexpected,
    )
    headers = {"Idempotency-Key": IDEMPOTENCY_KEY} if path.endswith("/preps") else None

    with _client() as client:
        response = client.request(method, path, headers=headers, json=json)

    assert response.status_code == expected_status
    assert response.json() == {
        "detail": (
            "Not found"
            if expected_status == status.HTTP_404_NOT_FOUND
            else "Visit Prep request is invalid."
        )
    }
    assert "private-malformed" not in response.text


def test_body_validation_never_echoes_contact_values(monkeypatch):
    private_contact = "private-contact-" + ("x" * 310) + "@example.test"

    def unexpected(**_kwargs):
        raise AssertionError("invalid contact must not reach the service")

    monkeypatch.setattr(route.visit_prep_service, "create_visit_prep", unexpected)
    payload = _create_payload()
    payload["recipient"] = {
        "first_name": "Ada",
        "email": private_contact,
    }

    with _client() as client:
        response = client.post(
            "/internal/v1/visit-prep/preps",
            headers={"Idempotency-Key": IDEMPOTENCY_KEY},
            json=payload,
        )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    assert response.json() == {"detail": "Visit Prep request is invalid."}
    assert private_contact not in response.text


@pytest.mark.parametrize(
    ("error", "expected_status", "expected_detail"),
    [
        (
            VisitPrepNotFound("private owner and identifier"),
            status.HTTP_404_NOT_FOUND,
            "Not found",
        ),
        (
            VisitPrepValidation("private contact value"),
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Visit Prep request is invalid.",
        ),
        (
            VisitPrepConflict("private generation state"),
            status.HTTP_409_CONFLICT,
            "Visit Prep operation conflicts with its current state.",
        ),
        (
            VisitPrepUnavailable("private key material"),
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Visit Prep is temporarily unavailable.",
        ),
        (
            SQLAlchemyError("private constraint and contact value"),
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Visit Prep is temporarily unavailable.",
        ),
    ],
)
def test_domain_and_persistence_errors_are_safely_mapped(
    monkeypatch,
    error: Exception,
    expected_status: int,
    expected_detail: str,
):
    def fail(**_kwargs):
        raise error

    monkeypatch.setattr(route.visit_prep_service, "create_visit_prep", fail)

    with _client() as client:
        response = client.post(
            "/internal/v1/visit-prep/preps",
            headers={"Idempotency-Key": IDEMPOTENCY_KEY},
            json=_create_payload(),
        )

    assert response.status_code == expected_status
    assert response.json() == {"detail": expected_detail}
    assert "private" not in response.text
    assert "constraint" not in response.text


def test_all_commands_use_the_view_as_safe_write_dependency(monkeypatch):
    app = FastAPI()
    app.include_router(
        route.visit_prep_preparations_api_app,
        prefix="/internal/v1",
    )

    def deny_write():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="View-as is read-only. Exit view-as before making changes.",
        )

    app.dependency_overrides[require_visit_prep_write] = deny_write

    def unexpected(**_kwargs):
        raise AssertionError("read-only view-as must not reach a mutation")

    for service_name in {
        "create_visit_prep",
        "rotate_visit_prep_credential",
        "revoke_visit_prep",
        "reschedule_visit_prep",
        "reconcile_visit_prep",
    }:
        monkeypatch.setattr(route.visit_prep_service, service_name, unexpected)

    requests = [
        (
            "POST",
            "/internal/v1/visit-prep/preps",
            {"Idempotency-Key": IDEMPOTENCY_KEY},
            _create_payload(),
        ),
        (
            "POST",
            f"/internal/v1/visit-prep/preps/{PREP_ID}/copy-link",
            None,
            {"expected_credential_generation": 1},
        ),
        (
            "POST",
            f"/internal/v1/visit-prep/preps/{PREP_ID}/revoke",
            None,
            {
                "expected_credential_generation": 1,
                "reason": "physician_revoked",
            },
        ),
        (
            "POST",
            f"/internal/v1/visit-prep/preps/{PREP_ID}/reschedule",
            None,
            {
                "expected_credential_generation": 1,
                "expected_schedule_generation": 1,
                "scheduled_at": SCHEDULED_AT.isoformat(),
                "timezone_name": "UTC",
            },
        ),
        (
            "PUT",
            f"/internal/v1/visit-prep/preps/{PREP_ID}/canonical-links",
            None,
            {"patient_id": str(PATIENT_ID)},
        ),
    ]

    with TestClient(app) as client:
        responses = [
            client.request(method, path, headers=headers, json=json)
            for method, path, headers, json in requests
        ]

    assert {response.status_code for response in responses} == {
        status.HTTP_403_FORBIDDEN
    }


def test_request_model_forbids_caller_supplied_ownership_and_actor(monkeypatch):
    def unexpected(**_kwargs):
        raise AssertionError("forbidden ownership fields must not reach the service")

    monkeypatch.setattr(route.visit_prep_service, "create_visit_prep", unexpected)
    payload = {
        **_create_payload(),
        "owner_practice_member_id": str(TypeID("practice_member")),
        "owner_doctor_id": str(TypeID("doctor")),
        "practice_id": str(TypeID("practice")),
        "actor_id": str(TypeID("doctor")),
    }

    with _client() as client:
        response = client.post(
            "/internal/v1/visit-prep/preps",
            headers={"Idempotency-Key": IDEMPOTENCY_KEY},
            json=payload,
        )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    assert response.json() == {"detail": "Visit Prep request is invalid."}
    assert "owner_practice_member_id" not in response.text
    assert "owner_doctor_id" not in response.text
    assert "actor_id" not in response.text


def test_access_url_uses_configured_domain_and_ignores_request_host(monkeypatch):
    monkeypatch.setattr(route, "INTAKE_CHAT_DOMAIN", "patient.example.test")
    monkeypatch.setattr(
        route.visit_prep_service,
        "create_visit_prep",
        lambda **_kwargs: _mutation("created", access_token=ACCESS_TOKEN),
    )

    with _client() as client:
        response = client.post(
            "/internal/v1/visit-prep/preps",
            headers={
                "Host": "attacker.example",
                "Forwarded": "host=also-attacker.example;proto=http",
                "X-Forwarded-Host": "forwarded-attacker.example",
                "Idempotency-Key": IDEMPOTENCY_KEY,
            },
            json=_create_payload(),
        )

    assert response.status_code == status.HTTP_201_CREATED
    access_url = response.json()["access_url"]
    assert urlsplit(access_url).netloc == "patient.example.test"
    assert "attacker" not in access_url
