from fastapi import status
from fastapi.testclient import TestClient
from polyfactory import Require
from polyfactory.factories import DataclassFactory
from polyfactory.factories.pydantic_factory import ModelFactory
from pydantic import EmailStr

from app.commands.intake.create import AppointmentRequest, AppointmentResponse
from app.server import api_app

from tests.factories import DoctorFactory


class AppointmentRequestFactory(ModelFactory[AppointmentRequest]):
    # optional by default, but make it required in the factory otherwise the randomness of doctor_{email,id} will cause us issues
    # TODO https://github.com/litestar-org/polyfactory/issues/655
    doctor_email: EmailStr


def assert_status(response, status_code: int = status.HTTP_200_OK):
    assert response.status_code == status_code, response.json()


def test_intake_creation(client: TestClient):
    request = AppointmentRequestFactory.build()

    user = DoctorFactory()
    user.generate_api_key()

    response = client.post(
        api_app.url_path_for("create_intake"),
        headers={"Authorization": f"Bearer {user.api_key}"},
        json=request.model_dump(mode="json"),
    )

    assert_status(response)

    data = response.json()
    assert data["url"].startswith("https://")


def test_intake_without_doctor_id(client: TestClient):
    request = AppointmentRequestFactory.build()

    user = DoctorFactory()
    user.generate_api_key()

    response = client.post(
        api_app.url_path_for("create_intake"),
        headers={"Authorization": f"Bearer {user.api_key}"},
        json=request.model_dump(mode="json")
        | {"doctor_id": None, "doctor_email": None},
    )

    assert_status(response, status.HTTP_422_UNPROCESSABLE_ENTITY)

    data = response.json()
    assert "detail" in data
    assert "doctor_id" in data["detail"][0]["msg"]
