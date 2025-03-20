import typing as t

from fastapi import status
from fastapi.testclient import TestClient
from polyfactory import BaseFactory, Ignore, Require
from polyfactory.decorators import post_generated
from polyfactory.factories import DataclassFactory
from polyfactory.factories.pydantic_factory import ModelFactory
from polyfactory.field_meta import FieldMeta
from pydantic import EmailStr
from typeid import TypeID

import app.commands.intake.chat
from app.commands.intake.create import AppointmentRequest, AppointmentResponse
from app.server import api_app

from activemodel.types import TypeIDType
from app.models.appointment import Appointment
from app.models.doctor import API_KEY_PREFIX, CLERK_OBJECT_PREFIX, Doctor
from app.models.intake.doctor_intake_schema import DoctorIntakeSchema, IntakeQuestion
from app.models.intake.intake import Intake
from app.models.patient import Patient

from tests.factory_alt import (
    EXAMPLE_INTAKE_QUESTIONS,
    AppointmentRequestFactory,
    DoctorFactory,
    DoctorIntakeSchemaFactory,
    SimpleIncompleteIntakeFactory,
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


def test_last_question(client: TestClient):
    """
    In order to test this, we need an intake with a valid structure (questions, etc)
    """
    intake = SimpleIncompleteIntakeFactory.build().save()

    # paranoid: learning the factory system
    assert not intake.is_new()

    result = app.commands.intake.chat.perform(intake, None)

    assert result
    assert result.question == EXAMPLE_INTAKE_QUESTIONS[0].question
    assert result.key == EXAMPLE_INTAKE_QUESTIONS[0].key


def test_next_question():
    intake = SimpleIncompleteIntakeFactory.build().save()

    # should be empty to start
    assert not intake.intake_questions_answers

    # send in a fake response
    fake_response = BaseFactory.__faker__.sentence()
    result = app.commands.intake.chat.perform(intake, fake_response)

    assert result
    # second q test
    assert result.question == EXAMPLE_INTAKE_QUESTIONS[1].question
    assert result.key == EXAMPLE_INTAKE_QUESTIONS[1].key

    intake.refresh()

    # now we should have an answer
    assert len(intake.intake_questions_answers) == 1
    assert (
        intake.intake_questions_answers[0].question_key
        == EXAMPLE_INTAKE_QUESTIONS[0].key
    )
    assert intake.intake_questions_answers[0].answer == fake_response
