from fastapi import status
from fastapi.testclient import TestClient

from app.server import api_app

from app.models.doctor import Doctor
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


def _confirmed_med(
    doctor, patient, name, kind=MedicationKind.prescription, confirmed=True
):
    return PatientMedication(
        doctor_id=doctor.id,
        patient_id=patient.id,
        name=name,
        normalized_name=name.lower(),
        kind=kind,
        source=MedicationSource.transcript,
        confirmed=confirmed,
    ).save()


def test_returns_depletions_with_citations(authenticated_client: TestClient):
    doctor = _authed_doctor()
    patient = PatientFactory.build(doctor_id=doctor.id).save()
    _confirmed_med(doctor, patient, "metformin")
    _confirmed_med(doctor, patient, "furosemide")

    resp = authenticated_client.get(
        api_app.url_path_for(
            "medication_nutrient_depletions", patient_id=str(patient.id)
        )
    )
    assert resp.status_code == status.HTTP_200_OK
    body = resp.json()
    assert body["disclaimer"]

    by_name = {m["name"]: m for m in body["medications"]}
    metformin = by_name["metformin"]
    assert metformin["depletions"][0]["nutrient"] == "Vitamin B12"
    assert metformin["depletions"][0]["citation"]["url"].startswith("https://")
    assert {d["nutrient"] for d in by_name["furosemide"]["depletions"]} >= {
        "Potassium",
        "Magnesium",
    }


def test_drug_without_known_depletion_is_omitted(authenticated_client: TestClient):
    doctor = _authed_doctor()
    patient = PatientFactory.build(doctor_id=doctor.id).save()
    _confirmed_med(doctor, patient, "acetaminophen")

    resp = authenticated_client.get(
        api_app.url_path_for(
            "medication_nutrient_depletions", patient_id=str(patient.id)
        )
    )
    assert resp.json()["medications"] == []


def test_supplements_and_unconfirmed_excluded(authenticated_client: TestClient):
    doctor = _authed_doctor()
    patient = PatientFactory.build(doctor_id=doctor.id).save()
    # a supplement is never a depletion source
    _confirmed_med(doctor, patient, "fish oil", kind=MedicationKind.supplement)
    # an unconfirmed metformin should not surface until confirmed
    _confirmed_med(doctor, patient, "metformin", confirmed=False)

    resp = authenticated_client.get(
        api_app.url_path_for(
            "medication_nutrient_depletions", patient_id=str(patient.id)
        )
    )
    assert resp.json()["medications"] == []


def test_other_doctors_patient_is_404(authenticated_client: TestClient):
    _authed_doctor()
    other = DoctorFactory.build().save()
    other_patient = PatientFactory.build(doctor_id=other.id).save()

    resp = authenticated_client.get(
        api_app.url_path_for(
            "medication_nutrient_depletions", patient_id=str(other_patient.id)
        )
    )
    assert resp.status_code == status.HTTP_404_NOT_FOUND
