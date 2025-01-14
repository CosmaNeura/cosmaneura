from fastapi import status
from fastapi.testclient import TestClient

from app.server import api_app

from app.models.appointment import Appointment


def test_notes_list(authenticated_client: TestClient):
    response = authenticated_client.get(api_app.url_path_for("note_list"))

    assert response.status_code == status.HTTP_200_OK

    result = response.json


def test_note_detail(authenticated_client: TestClient):
    # TODO will break as the seed process becomes more complex
    assert Appointment.count() == 1

    appointment = Appointment.first()

    response = authenticated_client.get(
        api_app.url_path_for("note_detail", appointment_id=appointment.id)
    )

    assert response.status_code == status.HTTP_200_OK

    result = response.json()
