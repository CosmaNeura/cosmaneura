from fastapi import status
from fastapi.testclient import TestClient

from app.server import api_app

from app.models.doctor import Doctor
from app.models.patient import Patient
from app.models.patient_medication import (
    MedicationKind,
    MedicationSource,
    PatientMedication,
)

from tests.factories import DoctorFactory, PatientFactory
from tests.routes.utils import get_clerk_dev_user


def _authed_doctor() -> Doctor:
    _, _, clerk_user = get_clerk_dev_user()
    return Doctor.find_or_create_by(clerk_id=clerk_user.id)


def _confirmed_med(doctor, patient, name, kind):
    return PatientMedication(
        doctor_id=doctor.id,
        patient_id=patient.id,
        name=name,
        normalized_name=name.lower(),
        kind=kind,
        source=MedicationSource.transcript,
        confirmed=True,
    ).save()


def test_panel_only_includes_confirmed_medications(authenticated_client: TestClient):
    doctor = _authed_doctor()
    patient = PatientFactory.build(doctor_id=doctor.id).save()

    _confirmed_med(doctor, patient, "metformin", MedicationKind.prescription)
    # unconfirmed row should be excluded
    PatientMedication(
        doctor_id=doctor.id,
        patient_id=patient.id,
        name="fish oil",
        normalized_name="fish oil",
        kind=MedicationKind.supplement,
        source=MedicationSource.transcript,
        confirmed=False,
    ).save()

    resp = authenticated_client.get(
        api_app.url_path_for(
            "patient_reference_resources", patient_id=str(patient.id)
        )
    )
    assert resp.status_code == status.HTTP_200_OK
    body = resp.json()

    assert body["directory"]  # always present
    meds = body["medications"]
    assert len(meds) == 1
    assert meds[0]["medication"]["name"] == "metformin"
    assert meds[0]["links"]


def test_natmed_links_follow_subscription_flag(authenticated_client: TestClient):
    doctor = _authed_doctor()
    patient = PatientFactory.build(doctor_id=doctor.id).save()
    _confirmed_med(doctor, patient, "metformin", MedicationKind.prescription)

    # default: no subscription → no NatMed links
    resp = authenticated_client.get(
        api_app.url_path_for(
            "patient_reference_resources", patient_id=str(patient.id)
        )
    )
    sources = {
        link["source"]
        for med in resp.json()["medications"]
        for link in med["links"]
    }
    assert "TRC Healthcare" not in sources

    # turn on the subscription flag via settings
    resp = authenticated_client.post(
        api_app.url_path_for("update_settings_data"),
        json={"biography": None, "has_natmed_subscription": True},
    )
    assert resp.status_code == status.HTTP_200_OK

    resp = authenticated_client.get(
        api_app.url_path_for(
            "patient_reference_resources", patient_id=str(patient.id)
        )
    )
    body = resp.json()
    assert body["has_natmed_subscription"] is True
    sources = {
        link["source"] for med in body["medications"] for link in med["links"]
    }
    assert "TRC Healthcare" in sources


def test_settings_exposes_natmed_flag(authenticated_client: TestClient):
    _authed_doctor()
    resp = authenticated_client.get(api_app.url_path_for("settings_data"))
    assert resp.status_code == status.HTTP_200_OK
    assert resp.json()["has_natmed_subscription"] is False


def test_other_doctors_patient_is_404(authenticated_client: TestClient):
    _authed_doctor()
    other = DoctorFactory.build().save()
    other_patient = PatientFactory.build(doctor_id=other.id).save()

    resp = authenticated_client.get(
        api_app.url_path_for(
            "patient_reference_resources", patient_id=str(other_patient.id)
        )
    )
    assert resp.status_code == status.HTTP_404_NOT_FOUND
