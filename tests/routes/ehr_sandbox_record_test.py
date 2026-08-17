from datetime import UTC, datetime

from fastapi import status
from fastapi.testclient import TestClient

from app.server import api_app
from app.services.ehr_sandbox_record_mapping import (
    provision_athena_preview_mapping,
    revoke_athena_preview_mapping,
)

from app.models.appointment import Appointment
from app.models.doctor import Doctor
from app.models.patient import Patient

from tests.routes.utils import get_clerk_dev_user

RECORD_REF = "a-195900.E-60178"


def _open_record() -> tuple[Doctor, Patient, Appointment]:
    _, _, clerk_user = get_clerk_dev_user()
    doctor = Doctor.find_or_create_by(clerk_id=clerk_user.id)
    patient = Patient(
        doctor_id=doctor.id,
        name="Synthetic Open Record",
        date_of_birth=datetime(2000, 1, 1, tzinfo=UTC),
    ).save()
    appointment = Appointment(
        doctor_id=doctor.id,
        patient_id=patient.id,
        appointment_date=datetime.now(UTC),
        external_id=None,
    ).save()
    return doctor, patient, appointment


def _url(appointment: Appointment) -> str:
    return api_app.url_path_for(
        "open_sandbox_labs_record",
        appointment_id=appointment.id,
    )


def _provision(doctor: Doctor, patient: Patient) -> None:
    provision_athena_preview_mapping(
        doctor_id=doctor.id,
        patient_id=patient.id,
        record_ref=RECORD_REF,
        operator_ref="operator_route_fixture",
    )


def test_open_record_returns_only_server_mapped_opaque_reference(
    authenticated_client: TestClient,
):
    doctor, patient, appointment = _open_record()
    _provision(doctor, patient)

    response = authenticated_client.get(_url(appointment))

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {"record_ref": RECORD_REF}
    assert "no-store" in response.headers["cache-control"]
    assert "Synthetic Open Record" not in response.text
    assert "date_of_birth" not in response.text

    operation = api_app.openapi()["paths"][
        "/internal/v1/notes/{appointment_id}/ehr-labs-record"
    ]["get"]
    assert "requestBody" not in operation
    assert [parameter["name"] for parameter in operation["parameters"]] == [
        "appointment_id"
    ]


def test_open_record_fails_closed_without_mapping_and_immediately_after_revocation(
    authenticated_client: TestClient,
):
    doctor, patient, appointment = _open_record()
    assert (
        authenticated_client.get(_url(appointment)).status_code
        == status.HTTP_404_NOT_FOUND
    )

    _provision(doctor, patient)
    assert authenticated_client.get(_url(appointment)).status_code == status.HTTP_200_OK
    revoke_athena_preview_mapping(
        doctor_id=doctor.id,
        patient_id=patient.id,
        operator_ref="operator_route_revoker",
    )

    revoked = authenticated_client.get(_url(appointment))
    assert revoked.status_code == status.HTTP_404_NOT_FOUND
    assert RECORD_REF not in revoked.text


def test_open_record_hides_another_doctors_appointment(
    authenticated_client: TestClient,
):
    other = Doctor(
        clerk_id="user_test_ehr_preview_route_other",
        email="ehr-preview-route-other@example.test",
        biography=None,
    ).save()
    patient = Patient(
        doctor_id=other.id,
        name="Other Synthetic Record",
        date_of_birth=datetime(2000, 1, 1, tzinfo=UTC),
    ).save()
    appointment = Appointment(
        doctor_id=other.id,
        patient_id=patient.id,
        appointment_date=datetime.now(UTC),
        external_id=None,
    ).save()
    _provision(other, patient)

    response = authenticated_client.get(_url(appointment))

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert RECORD_REF not in response.text


def test_open_record_requires_clerk_authentication(client: TestClient):
    _, _, appointment = _open_record()

    response = client.get(_url(appointment))

    assert response.status_code in {
        status.HTTP_401_UNAUTHORIZED,
        status.HTTP_403_FORBIDDEN,
    }
