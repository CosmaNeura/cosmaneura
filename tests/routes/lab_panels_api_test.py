from fastapi import status
from fastapi.testclient import TestClient

from app.server import api_app

from app.models.doctor import Doctor
from app.models.lab_panel import LabPanel, LabPanelStatus

from tests.factories import DoctorFactory, PatientFactory
from tests.routes.utils import get_clerk_dev_user


def _authed_doctor() -> Doctor:
    _, _, clerk_user = get_clerk_dev_user()
    return Doctor.find_or_create_by(clerk_id=clerk_user.id)


def test_manual_create_normalizes_and_lists(authenticated_client: TestClient):
    doctor = _authed_doctor()
    patient = PatientFactory.build(doctor_id=doctor.id).save()

    resp = authenticated_client.post(
        api_app.url_path_for("lab_panel_create", patient_id=str(patient.id)),
        json={
            "collected_on": "2026-07-01",
            "results": [
                {"name": "Vitamin D", "value": 28.5, "unit": "ng/mL"},
                {"name": "TSH", "value": 2.0, "unit": None},
            ],
        },
    )
    assert resp.status_code == status.HTTP_200_OK
    body = resp.json()
    assert body["source"] == "manual"
    assert body["status"] == "completed"
    assert body["collected_on"] == "2026-07-01"
    by_marker = {r["biomarker"]: r for r in body["results"]}
    assert by_marker["vitamin_d_25oh"]["value"] == 28.5
    assert by_marker["tsh"]["unit"] == "mIU/L"  # defaulted from registry

    resp = authenticated_client.get(
        api_app.url_path_for("lab_panel_list", patient_id=str(patient.id))
    )
    assert len(resp.json()) == 1
    assert len(resp.json()[0]["results"]) == 2


def test_create_rejects_bad_date(authenticated_client: TestClient):
    doctor = _authed_doctor()
    patient = PatientFactory.build(doctor_id=doctor.id).save()

    resp = authenticated_client.post(
        api_app.url_path_for("lab_panel_create", patient_id=str(patient.id)),
        json={"collected_on": "july 1st", "results": [{"name": "TSH", "value": 2.0}]},
    )
    assert resp.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


def test_upload_complete_creates_pending_panel_and_extracts(
    authenticated_client: TestClient, sync_celery
):
    doctor = _authed_doctor()
    patient = PatientFactory.build(doctor_id=doctor.id).save()

    resp = authenticated_client.post(
        api_app.url_path_for("lab_panel_upload_complete", patient_id=str(patient.id)),
        json={"file_name": "labs.pdf"},
    )
    assert resp.status_code == status.HTTP_200_OK
    # offline fallback → extraction completes with zero rows, panel not left pending
    panel = LabPanel.one(id=resp.json()["id"])
    assert panel.status == LabPanelStatus.completed


def test_delete(authenticated_client: TestClient):
    doctor = _authed_doctor()
    patient = PatientFactory.build(doctor_id=doctor.id).save()
    resp = authenticated_client.post(
        api_app.url_path_for("lab_panel_create", patient_id=str(patient.id)),
        json={"results": [{"name": "TSH", "value": 2.0}]},
    )
    panel_id = resp.json()["id"]

    resp = authenticated_client.delete(
        api_app.url_path_for("lab_panel_delete", lab_panel_id=panel_id)
    )
    assert resp.status_code == status.HTTP_204_NO_CONTENT

    resp = authenticated_client.get(
        api_app.url_path_for("lab_panel_list", patient_id=str(patient.id))
    )
    assert resp.json() == []


def test_other_doctors_patient_is_404(authenticated_client: TestClient):
    _authed_doctor()
    other = DoctorFactory.build().save()
    other_patient = PatientFactory.build(doctor_id=other.id).save()

    resp = authenticated_client.get(
        api_app.url_path_for("lab_panel_list", patient_id=str(other_patient.id))
    )
    assert resp.status_code == status.HTTP_404_NOT_FOUND
