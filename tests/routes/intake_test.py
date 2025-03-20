
from fastapi import status
from fastapi.testclient import TestClient

from app.server import api_app

from app.models.appointment import Appointment
from app.models.intake.intake import Intake

from tests.factories import (
    AppointmentRequestFactory,
    DoctorFactory,
    DoctorIntakeSchemaFactory,
)


# TODO move to general route assertions
def assert_status(response, status_code: int = status.HTTP_200_OK):
    assert response.status_code == status_code, response.json()


def test_intake_creation(client: TestClient):
    # paranoid
    assert Intake.count() == 0

    request = AppointmentRequestFactory.build(factory_use_construct=True)

    # use the same random email as the request
    user = DoctorFactory.build(email=request.doctor_email)
    user.generate_api_key()
    user.save()

    DoctorIntakeSchemaFactory.build(doctor_id=user.id).save()

    response = client.post(
        api_app.url_path_for("create_intake"),
        headers={"Authorization": f"Bearer {user.api_key}"},
        json=request.model_dump(mode="json"),
    )

    assert_status(response)

    # successful response should include a url to redirect the user to
    data = response.json()
    assert data["url"].startswith("https://")

    assert Intake.count() == 1
    intake = Intake.last()

    assert intake
    assert intake.doctor_id == user.id
    assert intake.appointment_id

    # TODO we can't do this since the session is temporal, need to fix in activemodel
    # appointment = intake.appointment

    appointment = Appointment.get(intake.appointment_id)

    assert appointment
    assert appointment.created_by_system == "api"

    # try posting again, and let's make sure nothing new is created
    response = client.post(
        api_app.url_path_for("create_intake"),
        headers={"Authorization": f"Bearer {user.api_key}"},
        json=request.model_dump(mode="json"),
    )

    assert_status(response)

    assert Intake.count() == 1


def test_intake_without_doctor_email(client: TestClient):
    request = AppointmentRequestFactory.build(factory_use_construct=True)

    # email on the above request will not exist
    user = DoctorFactory.build()
    user.generate_api_key()
    user.save()

    # we are intentionally not creating an intake schema since we should fail before then

    response = client.post(
        api_app.url_path_for("create_intake"),
        headers={"Authorization": f"Bearer {user.api_key}"},
        json=request.model_dump(mode="json"),
    )

    assert_status(response, status.HTTP_422_UNPROCESSABLE_ENTITY)

    data = response.json()
    assert "detail" in data
    assert "Doctor not found" in data["detail"][0]["msg"]


def test_intake_without_doctor_id(client: TestClient):
    request = AppointmentRequestFactory.build(factory_use_construct=True)

    user = DoctorFactory.build()
    user.generate_api_key()
    user.save()

    assert not user.deleted_at

    response = client.post(
        api_app.url_path_for("create_intake"),
        headers={"Authorization": f"Bearer {user.api_key}"},
        json=request.model_dump(mode="json")
        | {"doctor_id": None, "doctor_email": None},
    )

    assert_status(response, status.HTTP_422_UNPROCESSABLE_ENTITY)

    # the missing doctor ID should be specified in the error message
    data = response.json()
    assert "detail" in data
    assert "doctor_id" in data["detail"][0]["msg"]
