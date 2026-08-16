from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import status
from fastapi.testclient import TestClient
from jwt import InvalidTokenError
from structlog.testing import capture_logs

import app.jobs.score_engagement
import app.jobs.transcribe
from app.configuration.service_auth import (
    SCRIBE_TRANSCRIPT_WRITE_ROLE,
    EntraServiceAuthPolicy,
    EntraServiceAuthSettings,
)
from app.routes.dependencies.entra_service import (
    EntraServiceAuthenticator,
    EntraTokenDecoder,
    authenticate_scribe_service_request,
)
from app.server import api_app

from app.models.ai_visit_transcript import AIVisitTranscript
from app.models.appointment import Appointment
from app.models.doctor import Doctor
from app.models.patient import Patient
from app.models.scribe_transcript_delivery import (
    LOGICAL_SCRIBE_PRODUCER,
    ScribeTranscriptDelivery,
)
from sqlalchemy.exc import IntegrityError

from tests.routes.utils import get_clerk_dev_user

TENANT_ID = "11111111-1111-1111-1111-111111111111"
SCRIBE_CLIENT_ID = "22222222-2222-2222-2222-222222222222"
SCRIBE_RECONCILER_CLIENT_ID = "33333333-3333-3333-3333-333333333333"
UNAUTHORIZED_CLIENT_ID = "44444444-4444-4444-4444-444444444444"
VALID_TOKEN = "signed-entra-app-token"
RECONCILER_TOKEN = "signed-entra-reconciler-token"
UNAUTHORIZED_TOKEN = "signed-entra-unauthorized-token"


def _policy() -> EntraServiceAuthPolicy:
    return EntraServiceAuthPolicy(
        settings=EntraServiceAuthSettings(
            enabled=True,
            tenant_id=TENANT_ID,
            audience=str(uuid4()),
        ),
        allowed_client_ids=frozenset({SCRIBE_CLIENT_ID, SCRIBE_RECONCILER_CLIENT_ID}),
        required_role=SCRIBE_TRANSCRIPT_WRITE_ROLE,
        required_client_count=2,
        log_namespace="scribe_handoff",
    )


def _claims(
    *,
    client_id: str = SCRIBE_CLIENT_ID,
    roles: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "tid": TENANT_ID,
        "azp": client_id,
        "idtyp": "app",
        "roles": roles if roles is not None else [SCRIBE_TRANSCRIPT_WRITE_ROLE],
    }


def _decoder(token: str) -> dict[str, Any]:
    caller_by_token = {
        VALID_TOKEN: SCRIBE_CLIENT_ID,
        RECONCILER_TOKEN: SCRIBE_RECONCILER_CLIENT_ID,
        UNAUTHORIZED_TOKEN: UNAUTHORIZED_CLIENT_ID,
    }
    if token not in caller_by_token:
        raise InvalidTokenError
    return _claims(client_id=caller_by_token[token])


class StaticJwksClient:
    def __init__(self, key: object) -> None:
        self.key = key

    def get_signing_key_from_jwt(self, token: str) -> StaticJwksClient:
        assert token
        return self


@pytest.fixture
def scribe_client(client: TestClient):
    api_app.dependency_overrides[authenticate_scribe_service_request] = (
        EntraServiceAuthenticator(_policy(), decoder=_decoder)
    )
    yield client
    api_app.dependency_overrides.pop(authenticate_scribe_service_request, None)


def _appointment() -> Appointment:
    _, _, clerk_user = get_clerk_dev_user()
    doctor = Doctor.find_or_create_by(clerk_id=clerk_user.id)
    patient = Patient(
        doctor_id=doctor.id,
        name="Scribe Import Patient",
        date_of_birth=datetime.now(UTC),
    ).save()
    return Appointment(
        doctor_id=doctor.id,
        patient_id=patient.id,
        appointment_date=datetime.now(UTC),
        external_id=None,
    ).save()


def _payload(appointment: Appointment) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "encounter_id": str(appointment.id),
        "scribe_session_id": str(uuid4()),
        "duration_seconds": 3,
        "segments": [
            {
                "speaker": "Doctor",
                "content": "How have you been feeling?",
                "start_ms": 0,
                "end_ms": 1200,
            },
            {
                "speaker": "Patient",
                "content": "Much better this week.",
                "start_ms": 1400,
                "end_ms": 2900,
            },
        ],
    }


def _headers(
    *, token: str = VALID_TOKEN, idempotency_key: str = "scribe-import-1000"
) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Idempotency-Key": idempotency_key,
    }


def test_scribe_transcript_delivery_is_idempotent_and_queues_pipeline_once(
    scribe_client: TestClient,
    monkeypatch,
) -> None:
    appointment = _appointment()
    payload = _payload(appointment)
    queued_notes: list[object] = []
    queued_scores: list[object] = []
    monkeypatch.setattr(
        app.jobs.transcribe,
        "queue_schema_transcription",
        lambda transcript: queued_notes.append(transcript.id),
    )
    monkeypatch.setattr(
        app.jobs.score_engagement,
        "queue",
        lambda *, transcript_id: queued_scores.append(transcript_id),
    )
    url = api_app.url_path_for("import_scribe_transcript")

    first = scribe_client.post(url, headers=_headers(), json=payload)
    replay = scribe_client.post(url, headers=_headers(), json=payload)

    assert first.status_code == status.HTTP_201_CREATED
    assert first.json()["disposition"] == "created"
    assert replay.status_code == status.HTTP_200_OK
    assert replay.json() == {
        "schema_version": "1.0",
        "transcript_id": first.json()["transcript_id"],
        "disposition": "replayed",
    }
    assert (
        AIVisitTranscript.where(
            AIVisitTranscript.appointment_id == appointment.id
        ).count()
        == 1
    )
    assert (
        ScribeTranscriptDelivery.where(
            ScribeTranscriptDelivery.appointment_id == appointment.id
        ).count()
        == 1
    )
    assert queued_notes == [AIVisitTranscript.one(first.json()["transcript_id"]).id]
    assert queued_scores == queued_notes


def test_scribe_transcript_delivery_deduplicates_across_physical_callers(
    scribe_client: TestClient,
    monkeypatch,
) -> None:
    appointment = _appointment()
    payload = _payload(appointment)
    queued_notes: list[object] = []
    queued_scores: list[object] = []
    monkeypatch.setattr(
        app.jobs.transcribe,
        "queue_schema_transcription",
        lambda transcript: queued_notes.append(transcript.id),
    )
    monkeypatch.setattr(
        app.jobs.score_engagement,
        "queue",
        lambda *, transcript_id: queued_scores.append(transcript_id),
    )
    url = api_app.url_path_for("import_scribe_transcript")

    with capture_logs() as logs:
        first = scribe_client.post(url, headers=_headers(), json=payload)
        replay = scribe_client.post(
            url,
            headers=_headers(token=RECONCILER_TOKEN),
            json=payload,
        )

    assert first.status_code == status.HTTP_201_CREATED
    assert replay.status_code == status.HTTP_200_OK
    assert replay.json()["transcript_id"] == first.json()["transcript_id"]
    receipt = ScribeTranscriptDelivery.get(appointment_id=appointment.id)
    assert receipt is not None
    assert receipt.logical_producer == LOGICAL_SCRIBE_PRODUCER
    assert receipt.caller_client_id == SCRIBE_CLIENT_ID
    assert queued_notes == [receipt.transcript_id]
    assert queued_scores == [receipt.transcript_id]
    delivery_audits = [
        entry
        for entry in logs
        if entry.get("action", "").startswith("scribe_handoff.delivery_")
    ]
    assert [entry["action"] for entry in delivery_audits] == [
        "scribe_handoff.delivery_created",
        "scribe_handoff.delivery_replayed",
    ]
    assert [entry["actual_caller_client_id"] for entry in delivery_audits] == [
        SCRIBE_CLIENT_ID,
        SCRIBE_RECONCILER_CLIENT_ID,
    ]


@pytest.mark.parametrize("collision", ["idempotency_key", "scribe_session_id"])
def test_database_enforces_logical_identity_across_physical_callers(
    scribe_client: TestClient,
    monkeypatch,
    collision: str,
) -> None:
    appointment = _appointment()
    payload = _payload(appointment)
    monkeypatch.setattr(
        app.jobs.transcribe, "queue_schema_transcription", lambda transcript: None
    )
    monkeypatch.setattr(
        app.jobs.score_engagement, "queue", lambda *, transcript_id: None
    )
    response = scribe_client.post(
        api_app.url_path_for("import_scribe_transcript"),
        headers=_headers(idempotency_key="database-key-1000"),
        json=payload,
    )
    assert response.status_code == status.HTTP_201_CREATED
    receipt = ScribeTranscriptDelivery.get(appointment_id=appointment.id)
    assert receipt is not None

    with pytest.raises(IntegrityError):
        ScribeTranscriptDelivery(
            logical_producer=LOGICAL_SCRIBE_PRODUCER,
            caller_client_id=SCRIBE_RECONCILER_CLIENT_ID,
            idempotency_key=(
                receipt.idempotency_key
                if collision == "idempotency_key"
                else "different-database-key-1000"
            ),
            scribe_session_id=(
                receipt.scribe_session_id
                if collision == "scribe_session_id"
                else uuid4()
            ),
            appointment_id=receipt.appointment_id,
            transcript_id=receipt.transcript_id,
            payload_sha256=receipt.payload_sha256,
        ).save()


def test_scribe_transcript_retry_repairs_pipeline_dispatch(
    scribe_client: TestClient,
    monkeypatch,
) -> None:
    appointment = _appointment()
    payload = _payload(appointment)
    note_attempts: list[object] = []
    score_attempts: list[object] = []

    def fail_first_note_dispatch(transcript: AIVisitTranscript) -> None:
        note_attempts.append(transcript.id)
        if len(note_attempts) == 1:
            raise RuntimeError("synthetic broker outage")

    monkeypatch.setattr(
        app.jobs.transcribe,
        "queue_schema_transcription",
        fail_first_note_dispatch,
    )
    monkeypatch.setattr(
        app.jobs.score_engagement,
        "queue",
        lambda *, transcript_id: score_attempts.append(transcript_id),
    )
    url = api_app.url_path_for("import_scribe_transcript")

    with pytest.raises(RuntimeError, match="synthetic broker outage"):
        scribe_client.post(url, headers=_headers(), json=payload)
    replay = scribe_client.post(url, headers=_headers(), json=payload)

    assert replay.status_code == status.HTTP_200_OK
    assert replay.json()["disposition"] == "replayed"
    assert len(note_attempts) == 2
    assert score_attempts == [note_attempts[-1]]
    receipt = ScribeTranscriptDelivery.get(appointment_id=appointment.id)
    assert receipt is not None
    assert receipt.pipeline_dispatched_at is not None


def test_scribe_transcript_delivery_rejects_changed_retry(
    scribe_client: TestClient,
    monkeypatch,
) -> None:
    appointment = _appointment()
    payload = _payload(appointment)
    monkeypatch.setattr(
        app.jobs.transcribe, "queue_schema_transcription", lambda transcript: None
    )
    monkeypatch.setattr(
        app.jobs.score_engagement, "queue", lambda *, transcript_id: None
    )
    url = api_app.url_path_for("import_scribe_transcript")
    assert scribe_client.post(url, headers=_headers(), json=payload).status_code == 201

    payload["segments"][0]["content"] = "Different content"
    with capture_logs() as logs:
        response = scribe_client.post(
            url,
            headers=_headers(token=RECONCILER_TOKEN),
            json=payload,
        )

    assert response.status_code == status.HTTP_409_CONFLICT
    rejection = next(
        entry
        for entry in logs
        if entry.get("action") == "scribe_handoff.delivery_rejected"
    )
    assert rejection["error_class"] == "delivery_conflict"
    assert rejection["actual_caller_client_id"] == SCRIBE_RECONCILER_CLIENT_ID
    assert (
        AIVisitTranscript.where(
            AIVisitTranscript.appointment_id == appointment.id
        ).count()
        == 1
    )


def test_scribe_transcript_delivery_accepts_same_session_with_new_retry_key(
    scribe_client: TestClient,
    monkeypatch,
) -> None:
    appointment = _appointment()
    payload = _payload(appointment)
    monkeypatch.setattr(
        app.jobs.transcribe, "queue_schema_transcription", lambda transcript: None
    )
    monkeypatch.setattr(
        app.jobs.score_engagement, "queue", lambda *, transcript_id: None
    )
    url = api_app.url_path_for("import_scribe_transcript")

    assert scribe_client.post(url, headers=_headers(), json=payload).status_code == 201
    replay = scribe_client.post(
        url,
        headers=_headers(
            token=RECONCILER_TOKEN,
            idempotency_key="scribe-import-retry-1000",
        ),
        json=payload,
    )

    assert replay.status_code == status.HTTP_200_OK
    assert replay.json()["disposition"] == "replayed"


def test_scribe_transcript_delivery_rejects_clerk_or_missing_tokens(
    scribe_client: TestClient,
) -> None:
    payload = _payload(_appointment())
    url = api_app.url_path_for("import_scribe_transcript")

    missing = scribe_client.post(
        url,
        headers={"Idempotency-Key": "scribe-import-3000"},
        json=payload,
    )
    clerk = scribe_client.post(
        url,
        headers=_headers(token="test:user_test_clerk"),
        json=payload,
    )

    assert missing.status_code == status.HTTP_401_UNAUTHORIZED
    assert clerk.status_code == status.HTTP_401_UNAUTHORIZED


def test_scribe_transcript_delivery_requires_app_role(
    client: TestClient,
) -> None:
    def decoder_without_role(token: str) -> dict[str, Any]:
        assert token == VALID_TOKEN
        return _claims(roles=[])

    api_app.dependency_overrides[authenticate_scribe_service_request] = (
        EntraServiceAuthenticator(_policy(), decoder=decoder_without_role)
    )
    try:
        response = client.post(
            api_app.url_path_for("import_scribe_transcript"),
            headers=_headers(idempotency_key="scribe-import-4000"),
            json=_payload(_appointment()),
        )
    finally:
        api_app.dependency_overrides.pop(authenticate_scribe_service_request, None)

    assert response.status_code == status.HTTP_403_FORBIDDEN


def test_scribe_transcript_delivery_rejects_unallowlisted_app(
    client: TestClient,
) -> None:
    api_app.dependency_overrides[authenticate_scribe_service_request] = (
        EntraServiceAuthenticator(_policy(), decoder=_decoder)
    )
    try:
        with capture_logs() as logs:
            response = client.post(
                api_app.url_path_for("import_scribe_transcript"),
                headers=_headers(
                    token=UNAUTHORIZED_TOKEN,
                    idempotency_key="scribe-import-unauthorized-1000",
                ),
                json=_payload(_appointment()),
            )
    finally:
        api_app.dependency_overrides.pop(authenticate_scribe_service_request, None)

    assert response.status_code == status.HTTP_403_FORBIDDEN
    rejection = next(
        entry
        for entry in logs
        if entry.get("action") == "scribe_handoff.authorization_rejected"
    )
    assert rejection["actual_caller_client_id"] == UNAUTHORIZED_CLIENT_ID


@pytest.mark.parametrize(
    "policy",
    [
        replace(
            _policy(),
            allowed_client_ids=frozenset({SCRIBE_CLIENT_ID}),
        ),
        replace(
            _policy(),
            allowed_client_ids=frozenset({SCRIBE_CLIENT_ID, "not-a-uuid"}),
        ),
    ],
)
def test_scribe_transcript_delivery_fails_closed_for_incomplete_allowlist(
    client: TestClient,
    policy: EntraServiceAuthPolicy,
) -> None:
    api_app.dependency_overrides[authenticate_scribe_service_request] = (
        EntraServiceAuthenticator(policy, decoder=_decoder)
    )
    try:
        response = client.post(
            api_app.url_path_for("import_scribe_transcript"),
            headers=_headers(idempotency_key="scribe-import-incomplete-1000"),
            json=_payload(_appointment()),
        )
    finally:
        api_app.dependency_overrides.pop(authenticate_scribe_service_request, None)

    assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE


def test_entra_decoder_verifies_signature_issuer_and_audience() -> None:
    private_key = rsa.generate_private_key(public_exponent=65_537, key_size=2048)
    settings = _policy().settings
    now = datetime.now(UTC)
    token = jwt.encode(
        {
            **_claims(),
            "aud": settings.audience,
            "iss": settings.issuer,
            "sub": "scribe-service-principal",
            "iat": now,
            "exp": now + timedelta(minutes=5),
        },
        private_key,
        algorithm="RS256",
        headers={"kid": "test-key"},
    )
    decoder = EntraTokenDecoder(settings)
    decoder.jwks_client = StaticJwksClient(private_key.public_key())  # type: ignore[assignment]

    claims = decoder(token)

    assert claims["azp"] == SCRIBE_CLIENT_ID
    assert claims["aud"] == settings.audience


def test_scribe_transcript_delivery_does_not_accept_identity_assertions(
    scribe_client: TestClient,
) -> None:
    payload = _payload(_appointment())
    payload["patient_id"] = "patient_untrusted"

    response = scribe_client.post(
        api_app.url_path_for("import_scribe_transcript"),
        headers=_headers(idempotency_key="scribe-import-5000"),
        json=payload,
    )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    assert response.json() == {"detail": "Invalid service request."}
    assert "patient_untrusted" not in response.text
