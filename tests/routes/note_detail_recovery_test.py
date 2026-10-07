"""Unavailable Session Prep source links must not crash or reveal ownership."""

from datetime import datetime

from fastapi.testclient import TestClient
from typeid import TypeID

from app.server import api_app

from app.models.appointment import Appointment
from app.models.doctor import Doctor
from app.models.patient import Patient

from tests.routes.utils import get_clerk_dev_user


def test_missing_and_other_provider_visits_have_identical_safe_response(
    authenticated_client: TestClient,
):
    other_doctor = Doctor(
        clerk_id="user_synthetic_other_visit_owner", biography=None
    ).save()
    patient = Patient(
        doctor_id=other_doctor.id,
        name="Synthetic private source",
        date_of_birth=datetime(2000, 1, 1),
    ).save()
    appointment = Appointment(
        doctor_id=other_doctor.id,
        patient_id=patient.id,
        appointment_date=datetime(2026, 9, 23),
        external_id=None,
    ).save()
    missing_id = TypeID(prefix="appointment")
    responses = [
        authenticated_client.get(
            api_app.url_path_for("note_detail", appointment_id=value)
        )
        for value in (appointment.id, missing_id)
    ]
    for response in responses:
        assert response.status_code == 404
        assert response.json() == {"detail": "Visit unavailable"}
        assert "no-store" in response.headers["cache-control"]
        assert "Synthetic private source" not in response.text


def test_own_visit_is_still_readable_after_an_unavailable_link(
    authenticated_client: TestClient,
):
    _, _, clerk_user = get_clerk_dev_user()
    doctor = Doctor.find_or_create_by(clerk_id=clerk_user.id)
    patient = Patient(
        doctor_id=doctor.id,
        name="Synthetic accessible source",
        date_of_birth=datetime(2000, 1, 1),
    ).save()
    appointment = Appointment(
        doctor_id=doctor.id,
        patient_id=patient.id,
        appointment_date=datetime(2026, 9, 23),
        external_id=None,
    ).save()
    missing = authenticated_client.get(
        api_app.url_path_for("note_detail", appointment_id=TypeID(prefix="appointment"))
    )
    assert missing.status_code == 404
    response = authenticated_client.get(
        api_app.url_path_for("note_detail", appointment_id=appointment.id)
    )
    assert response.status_code == 200
    assert response.json()["appointment"]["id"] == str(appointment.id)
    assert "no-store" in response.headers["cache-control"]
