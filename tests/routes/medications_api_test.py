from datetime import datetime, timezone
from typing import cast

from fastapi import status
from fastapi.testclient import TestClient

import app.jobs.extract_medications as extract_medications_job
from app.server import api_app

from app.models.ai_visit_transcript import AIVisitTranscript, TranscriptEntry
from app.models.doctor import Doctor
from app.models.patient import Patient
from app.models.patient_medication import (
    MedicationSource,
    MedicationStatus,
    PatientMedication,
)

from tests.factories import AppointmentFactory, DoctorFactory, PatientFactory
from tests.routes.utils import get_clerk_dev_user


def _authed_doctor() -> Doctor:
    _, _, clerk_user = get_clerk_dev_user()
    return Doctor.find_or_create_by(clerk_id=clerk_user.id)


def _patient_for(doctor: Doctor) -> Patient:
    return PatientFactory.build(doctor_id=doctor.id).save()


def test_create_list_update_delete(authenticated_client: TestClient):
    doctor = _authed_doctor()
    patient = _patient_for(doctor)

    resp = authenticated_client.post(
        api_app.url_path_for("medication_create", patient_id=str(patient.id)),
        json={"name": "Metformin", "kind": "prescription", "dosage": "500mg"},
    )
    assert resp.status_code == status.HTTP_200_OK
    body = resp.json()
    assert body["name"] == "Metformin"
    assert body["confirmed"] is True  # manual rows are confirmed
    assert body["source"] == "manual"
    med_id = body["id"]

    resp = authenticated_client.get(
        api_app.url_path_for("medication_list", patient_id=str(patient.id))
    )
    assert resp.status_code == status.HTTP_200_OK
    assert [m["id"] for m in resp.json()] == [med_id]

    resp = authenticated_client.patch(
        api_app.url_path_for("medication_update", medication_id=med_id),
        json={"status": "stopped", "dosage": "discontinued"},
    )
    assert resp.status_code == status.HTTP_200_OK
    assert resp.json()["status"] == "stopped"

    resp = authenticated_client.delete(
        api_app.url_path_for("medication_delete", medication_id=med_id)
    )
    assert resp.status_code == status.HTTP_204_NO_CONTENT

    resp = authenticated_client.get(
        api_app.url_path_for("medication_list", patient_id=str(patient.id))
    )
    assert resp.json() == []


def test_confirming_sets_confirmed_flag(authenticated_client: TestClient):
    doctor = _authed_doctor()
    patient = _patient_for(doctor)
    row = PatientMedication(
        doctor_id=doctor.id,
        patient_id=patient.id,
        name="metformin",
        normalized_name="metformin",
        source=MedicationSource.transcript,
        confirmed=False,
    ).save()

    resp = authenticated_client.patch(
        api_app.url_path_for("medication_update", medication_id=str(row.id)),
        json={"confirmed": True},
    )
    assert resp.status_code == status.HTTP_200_OK
    assert resp.json()["confirmed"] is True

    row = PatientMedication.one(id=row.id)
    assert row.confirmed is True
    assert row.confirmed_at is not None


def test_other_doctors_patient_is_404(authenticated_client: TestClient):
    _authed_doctor()
    other_doctor = DoctorFactory.build().save()
    other_patient = _patient_for(other_doctor)

    resp = authenticated_client.get(
        api_app.url_path_for("medication_list", patient_id=str(other_patient.id))
    )
    assert resp.status_code == status.HTTP_404_NOT_FOUND


def test_extract_endpoint_queues_job(
    authenticated_client: TestClient, sync_celery, monkeypatch
):
    # The offline extraction stand-in fires only when PYTHON_ENV=test AND the
    # Azure OpenAI credentials are absent. CI has them, so this test was calling
    # the live gpt-4.1 deployment and failing whenever it hit a 429 — the point
    # here is that the endpoint queues the job, not that the model responds.
    monkeypatch.setattr(
        extract_medications_job, "should_use_test_openai_fallback", lambda: True
    )
    doctor = _authed_doctor()
    patient = _patient_for(doctor)
    appointment = AppointmentFactory.build(
        doctor_id=doctor.id, patient_id=patient.id, appointment_date=datetime.now()
    ).save()
    AIVisitTranscript(
        doctor_id=doctor.id,
        appointment_id=appointment.id,
        patient_id=patient.id,
        duration=60,
        transcript_data=[
            TranscriptEntry(
                speaker="Patient",
                content="I take lisinopril and ibuprofen.",
                startTime=0,
                endTime=1,
            )
        ],
    ).save()

    resp = authenticated_client.post(
        api_app.url_path_for("medication_extract", patient_id=str(patient.id)),
        json={"appointment_id": str(appointment.id)},
    )
    assert resp.status_code == status.HTTP_200_OK

    rows = cast(
        list[PatientMedication],
        list(PatientMedication.where(PatientMedication.patient_id == patient.id).all()),
    )
    names = {r.normalized_name for r in rows}
    assert "lisinopril" in names
    assert "ibuprofen" in names


def test_owner_hold_resume_preserves_source_and_other_confirmed_states(
    authenticated_client: TestClient,
):
    doctor = _authed_doctor()
    patient = _patient_for(doctor)
    source = AppointmentFactory.build(
        doctor_id=doctor.id,
        patient_id=patient.id,
        appointment_date=datetime(2026, 10, 31, 12, 0),
    ).save()
    confirmation = datetime(2026, 10, 31, 13, 0, tzinfo=timezone.utc)
    rows = [
        PatientMedication(
            doctor_id=doctor.id,
            patient_id=patient.id,
            appointment_id=source.id,
            name="Synthetic medication",
            status=value,
            source=MedicationSource.transcript,
            confirmed=True,
            confirmed_at=confirmation,
        ).save()
        for value in (
            MedicationStatus.active,
            MedicationStatus.stopped,
            MedicationStatus.held,
        )
    ]
    endpoint = api_app.url_path_for("medication_update", medication_id=str(rows[0].id))
    for target in ("held", "active"):
        response = authenticated_client.patch(endpoint, json={"status": target})
        assert response.status_code == status.HTTP_200_OK
        body = response.json()
        assert body["status"] == target and body["confirmed"] is True
        assert body["appointment_id"] == str(source.id)
        assert (
            datetime.fromisoformat(body["source_appointment_date"])
            == source.appointment_date
        )
        assert datetime.fromisoformat(body["confirmed_at"]) == confirmation
        assert body["list_updated_at"] is not None
        row = PatientMedication.one(id=rows[0].id)
        assert row.status.value == target and row.confirmed
        assert row.confirmed_at == confirmation
    assert PatientMedication.one(id=rows[1].id).status == MedicationStatus.stopped
    assert PatientMedication.one(id=rows[2].id).status == MedicationStatus.held
    response = authenticated_client.get(
        api_app.url_path_for("medication_list", patient_id=str(patient.id))
    )
    assert response.status_code == status.HTTP_200_OK
    assert {m["id"] for m in response.json()} == {str(row.id) for row in rows}
    assert {m["status"] for m in response.json()} == {"active", "stopped", "held"}


def test_owner_confirms_held_suggestion_and_manual_held_entry(
    authenticated_client: TestClient,
):
    doctor = _authed_doctor()
    patient = _patient_for(doctor)
    row = PatientMedication(
        doctor_id=doctor.id,
        patient_id=patient.id,
        name="Synthetic metformin",
        source=MedicationSource.transcript,
        status=MedicationStatus.stopped,
    ).save()
    endpoint = api_app.url_path_for("medication_update", medication_id=str(row.id))
    response = authenticated_client.patch(endpoint, json={"status": "held"})
    assert response.status_code == status.HTTP_200_OK
    assert response.json()["status"] == "held" and not response.json()["confirmed"]
    response = authenticated_client.patch(endpoint, json={"confirmed": True})
    assert response.status_code == status.HTTP_200_OK
    assert response.json()["status"] == "held" and response.json()["confirmed"]
    assert response.json()["confirmed_at"] is not None
    response = authenticated_client.post(
        api_app.url_path_for("medication_create", patient_id=str(patient.id)),
        json={"name": "Synthetic held entry", "status": "held"},
    )
    assert response.status_code == status.HTTP_200_OK
    assert response.json()["status"] == "held" and response.json()["confirmed"]
    assert response.json()["source"] == "manual"


def test_foreign_hold_resume_and_create_are_denied(authenticated_client: TestClient):
    _authed_doctor()
    other = DoctorFactory.build().save()
    patient = _patient_for(other)
    row = PatientMedication(
        doctor_id=other.id,
        patient_id=patient.id,
        name="Synthetic",
        status=MedicationStatus.held,
        confirmed=True,
    ).save()
    endpoint = api_app.url_path_for("medication_update", medication_id=str(row.id))
    for target in ("active", "held"):
        response = authenticated_client.patch(
            endpoint, json={"status": target, "confirmed": True}
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND
    response = authenticated_client.post(
        api_app.url_path_for("medication_create", patient_id=str(patient.id)),
        json={"name": "Synthetic", "status": "held"},
    )
    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert PatientMedication.one(id=row.id).status == MedicationStatus.held
    assert (
        PatientMedication.where(PatientMedication.patient_id == patient.id).count() == 1
    )
