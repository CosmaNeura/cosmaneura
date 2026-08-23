import datetime

from fastapi import status
from fastapi.testclient import TestClient

from app.server import api_app

from app.models.appointment import Appointment
from app.models.intake.intake import Intake

from tests.factories import (
    AppointmentRequestFactory,
    DoctorFactory,
    PastAppointmentRequestFactory,
)
from tests.routes.utils import assert_status, provision_practice_in_force


def test_intake_creation(client: TestClient, sync_celery):
    "test a successful intake creation"

    initial_intake_count = Intake.count()

    request_json = AppointmentRequestFactory.build_json()

    # use the same random email as the request
    user = DoctorFactory.build(email=request_json["doctor_email"])
    user.generate_api_key()
    user.save()
    provision_practice_in_force(user)

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


def test_intake_cannot_target_a_different_doctor_email(client: TestClient):
    # factory_use_contruct=True skips validations
    request = AppointmentRequestFactory.build(factory_use_construct=True)

    assert request.doctor is None

    # email on the above request will not exist
    user = DoctorFactory.build()
    user.generate_api_key()
    user.save()
    provision_practice_in_force(user)

    # we are intentionally not creating an intake schema since we should fail before then
    initial_intake_count = Intake.count()

    response = client.post(
        api_app.url_path_for("create_intake"),
        headers={"Authorization": f"Bearer {user.api_key}"},
        json=request.model_dump(mode="json"),
    )

    assert_status(response, status.HTTP_403_FORBIDDEN)
    assert Intake.count() == initial_intake_count


def test_intake_without_doctor_id(client: TestClient):
    request = AppointmentRequestFactory.build(factory_use_construct=True)

    user = DoctorFactory.build()
    user.generate_api_key()
    user.save()
    provision_practice_in_force(user)

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
    assert any("doctor_id" in err.get("msg", "") for err in data["detail"])


def test_intake_with_past_date(client: TestClient):
    request = PastAppointmentRequestFactory.build(factory_use_construct=True)
    assert request.date <= datetime.datetime.now()

    user = DoctorFactory.build(email=request.doctor_email)
    user.generate_api_key()
    user.save()
    provision_practice_in_force(user)

    response = client.post(
        api_app.url_path_for("create_intake"),
        headers={"Authorization": f"Bearer {user.api_key}"},
        json=request.model_dump(mode="json"),
    )

    assert_status(response, status.HTTP_422_UNPROCESSABLE_ENTITY)

    body = response.json()
    assert "detail" in body, body

    assert any(
        "Appointment date must be in the future" in err.get("msg", "")
        for err in body["detail"]
    )
