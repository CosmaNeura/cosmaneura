import json

from fastapi import status
from fastapi.testclient import TestClient
from pydantic import json as pydantic_json

from app.server import api_app

from app.models.appointment import Appointment
from app.models.intake.intake import Intake

from tests.factories import (
    EXAMPLE_INTAKE_QUESTIONS,
    AppointmentRequestFactory,
    DoctorFactory,
)
from tests.routes.utils import assert_status


def test_intake_creation(client: TestClient, sync_celery):
    "test a successful intake creation"

    initial_intake_count = Intake.count()

    request_json = AppointmentRequestFactory.build_json()

    # use the same random email as the request
    user = DoctorFactory.build(email=request_json["doctor_email"])
    user.generate_api_key()
    user.save()

    response = client.post(
        api_app.url_path_for("create_intake"),
        headers={"Authorization": f"Bearer {user.api_key}"},
        json=request_json,
    )

    assert_status(response)

    # TODO assert more details on url once that is settled
    # successful response should include a url to redirect the user to
    data = response.json()
    assert data["url"].startswith("https://")

    assert Intake.count() == initial_intake_count + 1
    intake = Intake.get(data["id"])

    assert intake
    assert intake.doctor_id == user.id
    assert intake.appointment_id
    assert len(intake.intake_questions) > 0
    assert len(intake.external_intake_data.keys()) > 0

    # TODO we can't do this since the session is temporal, need to fix in activemodel
    # appointment = intake.appointment

    appointment = Appointment.get(intake.appointment_id)

    assert appointment
    assert appointment.created_by_system == "api"

    # try posting again, and let's make sure nothing new is created
    response = client.post(
        api_app.url_path_for("create_intake"),
        headers={"Authorization": f"Bearer {user.api_key}"},
        json=request_json,
    )

    assert_status(response)

    # let's make sure new intakes weren't created
    assert Intake.count() == initial_intake_count + 1


def test_intake_without_doctor_email(client: TestClient):
    # factory_use_contruct=True skips validations
    request = AppointmentRequestFactory.build(factory_use_construct=True)

    assert request.doctor is None

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
