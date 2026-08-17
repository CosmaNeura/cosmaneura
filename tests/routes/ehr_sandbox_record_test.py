import json
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from cryptography.fernet import Fernet
from fastapi import Request, status
from fastapi.testclient import TestClient

from app.routes.dependencies.login_as import login_as
from app.server import api_app
from app.services.ehr_sandbox_record_mapping import (
    designate_athena_preview_patient,
    provision_athena_preview_mapping,
    revoke_athena_preview_mapping,
)

from app.models.appointment import Appointment
from app.models.doctor import Doctor
from app.models.patient import Patient

from tests.routes.utils import get_clerk_dev_user

RECORD_REF = "a-195900.E-60178"
GRANT_KEY = Fernet.generate_key()


@pytest.fixture(autouse=True)
def _record_grant_key(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SOVEREIGN_RECORD_GRANT_SECRET", GRANT_KEY.decode("ascii"))


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
    designate_athena_preview_patient(
        doctor_id=doctor.id,
        patient_id=patient.id,
        operator_ref="operator_route_designator",
    )
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
    assert set(response.json()) == {"record_ref"}
    grant = response.json()["record_ref"]
    assert RECORD_REF not in grant
    claims = json.loads(Fernet(GRANT_KEY).decrypt(grant.encode("ascii"), ttl=60))
    assert claims["vendor_record_ref"] == RECORD_REF
    assert claims["actor_ref"] == doctor.clerk_id
    assert claims["doctor_ref"] == str(doctor.id)
    assert claims["appointment_ref"] == str(appointment.id)
    assert claims["patient_ref"] == str(patient.id)
    assert claims["audience"] == "cosmaneura-sovereign"
    assert claims["issuer"] == "cosmaneura-platform"
    assert claims["adapter"] == "athena"
    assert claims["environment"] == "preview"
    assert claims["practice_ref"] == "195900"
    assert claims["synthetic_only"] is True
    assert claims["tier"] == "sandbox"
    assert claims["version"] == 1
    assert str(UUID(claims["nonce"])) == claims["nonce"]
    issued_at = datetime.fromisoformat(claims["issued_at"].replace("Z", "+00:00"))
    expires_at = datetime.fromisoformat(claims["expires_at"].replace("Z", "+00:00"))
    assert expires_at - issued_at == timedelta(seconds=60)
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
    schema = api_app.openapi()["components"]["schemas"]["OpenSandboxRecord"][
        "properties"
    ]["record_ref"]
    assert schema["minLength"] == 80
    assert schema["maxLength"] == 4096
    assert schema["pattern"] == "^[A-Za-z0-9_-]{80,4096}={0,2}$"
    assert "must not parse" in schema["description"]


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


@pytest.mark.parametrize(
    "malformed_key",
    [
        "",
        "not-a-fernet-key",
        f" {GRANT_KEY.decode('ascii')}",
        f"{GRANT_KEY.decode('ascii')}\n",
    ],
    ids=["missing", "malformed", "leading-whitespace", "trailing-whitespace"],
)
def test_open_record_fails_closed_for_noncanonical_grant_key(
    authenticated_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    malformed_key: str,
):
    doctor, patient, appointment = _open_record()
    _provision(doctor, patient)
    monkeypatch.setenv("SOVEREIGN_RECORD_GRANT_SECRET", malformed_key)

    response = authenticated_client.get(_url(appointment))

    assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert RECORD_REF not in response.text


def test_open_record_refuses_admin_login_as_as_an_ehr_authority(
    authenticated_client: TestClient,
):
    target = Doctor(
        clerk_id="user_test_ehr_preview_impersonated",
        email="ehr-preview-impersonated@example.test",
        biography=None,
    ).save()
    patient = Patient(
        doctor_id=target.id,
        name="Synthetic Impersonated Record",
        date_of_birth=datetime(2000, 1, 1, tzinfo=UTC),
    ).save()
    appointment = Appointment(
        doctor_id=target.id,
        patient_id=patient.id,
        appointment_date=datetime.now(UTC),
        external_id=None,
    ).save()
    _provision(target, patient)

    def force_login_as(request: Request) -> None:
        request.state.doctor = target

    api_app.dependency_overrides[login_as] = force_login_as
    try:
        response = authenticated_client.get(_url(appointment))
    finally:
        del api_app.dependency_overrides[login_as]

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert RECORD_REF not in response.text
