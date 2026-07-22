from fastapi import status
from fastapi.testclient import TestClient

from app.server import api_app

from app.models.doctor import Doctor
from app.models.patient import Patient

from tests.factories import DoctorFactory, PatientFactory
from tests.routes.utils import get_clerk_dev_user


def _authed_doctor() -> Doctor:
    _, _, clerk_user = get_clerk_dev_user()
    return Doctor.find_or_create_by(clerk_id=clerk_user.id)


def _patient_for(doctor: Doctor) -> Patient:
    return PatientFactory.build(doctor_id=doctor.id).save()


def test_generate_then_fetch_latest(authenticated_client: TestClient, sync_celery):
    doctor = _authed_doctor()
    patient = _patient_for(doctor)

    resp = authenticated_client.post(
        api_app.url_path_for("generate_session_prep"),
        json={"patient_id": str(patient.id)},
    )
    assert resp.status_code == status.HTTP_200_OK

    resp = authenticated_client.get(
        api_app.url_path_for("session_prep_latest", patient_id=str(patient.id))
    )
    assert resp.status_code == status.HTTP_200_OK
    body = resp.json()
    assert body["status"] == "completed"
    assert body["talking_points"]
    assert body["alerts"] == []


def test_latest_404_when_never_generated(authenticated_client: TestClient):
    doctor = _authed_doctor()
    patient = _patient_for(doctor)

    resp = authenticated_client.get(
        api_app.url_path_for("session_prep_latest", patient_id=str(patient.id))
    )
    assert resp.status_code == status.HTTP_404_NOT_FOUND


def test_other_doctors_patient_is_404(authenticated_client: TestClient):
    _authed_doctor()
    other_doctor = DoctorFactory.build().save()
    other_patient = _patient_for(other_doctor)

    resp = authenticated_client.post(
        api_app.url_path_for("generate_session_prep"),
        json={"patient_id": str(other_patient.id)},
    )
    assert resp.status_code == status.HTTP_404_NOT_FOUND

    resp = authenticated_client.get(
        api_app.url_path_for("session_prep_latest", patient_id=str(other_patient.id))
    )
    assert resp.status_code == status.HTTP_404_NOT_FOUND
