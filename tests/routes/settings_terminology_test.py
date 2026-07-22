from fastapi import status
from fastapi.testclient import TestClient

from app.server import api_app

from app.models.doctor import Doctor, Terminology

from tests.routes.utils import get_clerk_dev_user


def _authed_doctor() -> Doctor:
    _, _, clerk_user = get_clerk_dev_user()
    return Doctor.find_or_create_by(clerk_id=clerk_user.id)


def test_settings_defaults_to_doctor_patient(authenticated_client: TestClient):
    _authed_doctor()

    resp = authenticated_client.get(api_app.url_path_for("settings_data"))
    assert resp.status_code == status.HTTP_200_OK
    assert resp.json()["terminology"] == "doctor_patient"


def test_update_terminology_round_trips(authenticated_client: TestClient):
    doctor = _authed_doctor()

    resp = authenticated_client.post(
        api_app.url_path_for("update_settings_data"),
        json={"biography": "behavioral health", "terminology": "therapist_client"},
    )
    assert resp.status_code == status.HTTP_200_OK

    doctor = Doctor.one(id=doctor.id)
    assert doctor.terminology == Terminology.therapist_client

    resp = authenticated_client.get(api_app.url_path_for("settings_data"))
    assert resp.json()["terminology"] == "therapist_client"


def test_update_without_terminology_preserves_existing(
    authenticated_client: TestClient,
):
    doctor = _authed_doctor()
    doctor.terminology = Terminology.counselor_client
    doctor.save()

    resp = authenticated_client.post(
        api_app.url_path_for("update_settings_data"),
        json={"biography": "updated bio only"},
    )
    assert resp.status_code == status.HTTP_200_OK

    doctor = Doctor.one(id=doctor.id)
    assert doctor.terminology == Terminology.counselor_client
    assert doctor.biography == "updated bio only"


def test_update_rejects_unknown_terminology(authenticated_client: TestClient):
    _authed_doctor()

    resp = authenticated_client.post(
        api_app.url_path_for("update_settings_data"),
        json={"biography": None, "terminology": "physician_patient"},
    )
    assert resp.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
