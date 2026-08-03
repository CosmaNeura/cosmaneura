from datetime import datetime
from typing import cast

from fastapi import status
from fastapi.testclient import TestClient

from app.server import api_app

from app.models.ai_visit_transcript import AIVisitTranscript, TranscriptEntry
from app.models.doctor import Doctor
from app.models.patient import Patient
from app.models.patient_medication import (
    MedicationSource,
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


def test_extract_endpoint_queues_job(authenticated_client: TestClient, sync_celery):
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
