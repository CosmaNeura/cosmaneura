from fastapi import status
from fastapi.testclient import TestClient

from app.server import api_app

from app.models.doctor import Doctor
from app.models.drug_reference import DrugReference
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


def test_ready_pending_and_supplement_excluded(authenticated_client: TestClient):
    doctor = _authed_doctor()
    patient = PatientFactory.build(doctor_id=doctor.id).save()

    # cached drug → ready
    _confirmed_med(doctor, patient, "metformin", MedicationKind.prescription)
    DrugReference(
        normalized_name="metformin",
        sections=[
            {
                "key": "adverse_reactions",
                "label": "Adverse reactions",
                "text": "Nausea, B12 depletion.",
            }
        ],
        found=True,
    ).save()

    # uncached drug → pending (and queues a fetch)
    _confirmed_med(doctor, patient, "lisinopril", MedicationKind.prescription)

    # supplement → excluded entirely
    _confirmed_med(doctor, patient, "fish oil", MedicationKind.supplement)

    resp = authenticated_client.get(
        api_app.url_path_for("medication_drug_reference", patient_id=str(patient.id))
    )
    assert resp.status_code == status.HTTP_200_OK
    body = resp.json()
    by_name = {m["name"]: m for m in body["medications"]}

    assert "fish oil" not in by_name  # supplement excluded
    assert by_name["metformin"]["status"] == "ready"
    assert "B12" in by_name["metformin"]["sections"][0]["text"]
    assert by_name["lisinopril"]["status"] == "pending"
    assert by_name["lisinopril"]["sections"] == []


def test_negative_cache_reports_none(authenticated_client: TestClient):
    doctor = _authed_doctor()
    patient = PatientFactory.build(doctor_id=doctor.id).save()
    _confirmed_med(doctor, patient, "obscuredrug", MedicationKind.prescription)
    DrugReference(normalized_name="obscuredrug", sections=[], found=False).save()

    resp = authenticated_client.get(
        api_app.url_path_for("medication_drug_reference", patient_id=str(patient.id))
    )
    body = resp.json()
    assert body["medications"][0]["status"] == "none"


def test_unconfirmed_medication_excluded(authenticated_client: TestClient):
    doctor = _authed_doctor()
    patient = PatientFactory.build(doctor_id=doctor.id).save()
    PatientMedication(
        doctor_id=doctor.id,
        patient_id=patient.id,
        name="metformin",
        normalized_name="metformin",
        kind=MedicationKind.prescription,
        source=MedicationSource.transcript,
        confirmed=False,
    ).save()

    resp = authenticated_client.get(
        api_app.url_path_for("medication_drug_reference", patient_id=str(patient.id))
    )
    assert resp.json()["medications"] == []


def test_other_doctors_patient_is_404(authenticated_client: TestClient):
    _authed_doctor()
    other = DoctorFactory.build().save()
    other_patient = PatientFactory.build(doctor_id=other.id).save()

    resp = authenticated_client.get(
        api_app.url_path_for(
            "medication_drug_reference", patient_id=str(other_patient.id)
        )
    )
    assert resp.status_code == status.HTTP_404_NOT_FOUND
