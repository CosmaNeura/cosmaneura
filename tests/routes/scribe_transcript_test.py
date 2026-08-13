from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import status
from fastapi.testclient import TestClient

import app.jobs.score_engagement
import app.jobs.transcribe
from app.server import api_app

from app.models.ai_visit_transcript import AIVisitTranscript
from app.models.appointment import Appointment
from app.models.doctor import Doctor
from app.models.patient import Patient

from tests.routes.utils import get_clerk_dev_user


def _appointment_for_authenticated_doctor() -> Appointment:
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
        "doctor_id": str(appointment.doctor_id),
        "appointment_id": str(appointment.id),
        "patient_id": str(appointment.patient_id),
        "duration": 3,
        "transcript_data": [
            {
                "speaker": "Doctor",
                "content": "How have you been feeling?",
                "startTime": 0,
                "endTime": 1200,
            },
            {
                "speaker": "Patient",
                "content": "Much better this week.",
                "startTime": 1400,
                "endTime": 2900,
            },
        ],
    }


def test_scribe_transcript_import_is_idempotent_and_queues_note_pipeline_once(
    authenticated_client: TestClient,
    monkeypatch,
) -> None:
    appointment = _appointment_for_authenticated_doctor()
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

    first = authenticated_client.post(
        api_app.url_path_for("import_scribe_transcript"),
        headers={"Idempotency-Key": "scribe-import-1000"},
        json=_payload(appointment),
    )
    replay = authenticated_client.post(
        api_app.url_path_for("import_scribe_transcript"),
        headers={"Idempotency-Key": "scribe-import-1000"},
        json=_payload(appointment),
    )

    assert first.status_code == status.HTTP_200_OK
    assert replay.status_code == status.HTTP_200_OK
    assert replay.json()["id"] == first.json()["id"]
    assert (
        AIVisitTranscript.where(
            AIVisitTranscript.appointment_id == appointment.id
        ).count()
        == 1
    )
    assert queued_notes == [AIVisitTranscript.one(first.json()["id"]).id]
    assert queued_scores == queued_notes


def test_scribe_transcript_import_rejects_different_existing_content(
    authenticated_client: TestClient,
    monkeypatch,
) -> None:
    appointment = _appointment_for_authenticated_doctor()
    monkeypatch.setattr(
        app.jobs.transcribe, "queue_schema_transcription", lambda transcript: None
    )
    monkeypatch.setattr(
        app.jobs.score_engagement, "queue", lambda *, transcript_id: None
    )
    url = api_app.url_path_for("import_scribe_transcript")
    headers = {"Idempotency-Key": "scribe-import-2000"}
    assert (
        authenticated_client.post(
            url, headers=headers, json=_payload(appointment)
        ).status_code
        == 200
    )

    conflicting = _payload(appointment)
    conflicting["transcript_data"][0]["content"] = "Different content"
    response = authenticated_client.post(url, headers=headers, json=conflicting)

    assert response.status_code == status.HTTP_409_CONFLICT
    assert (
        AIVisitTranscript.where(
            AIVisitTranscript.appointment_id == appointment.id
        ).count()
        == 1
    )
