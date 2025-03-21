from fastapi import status
from fastapi.testclient import TestClient
from polyfactory import BaseFactory

import app.commands.intake.chat

from app.models.intake.intake import Intake

from tests.factories import (
    EXAMPLE_INTAKE_QUESTIONS,
    AppointmentFactory,
    DoctorFactory,
    SimpleIncompleteIntakeFactory,
)
from tests.integration.server import api_app
from tests.routes.utils import assert_status


def test_invalid_intake_uuid(client: TestClient):
    response = client.get(api_app.url_path_for("get_last_question", intake_id="BAD_ID"))

    assert_status(response, status.HTTP_404_NOT_FOUND)


def test_uuid_cannot_be_found(client: TestClient):
    # TODO so ugly, there has to be a better way...
    # TypeID(Intake.model_fields["id"].sa_column.type.prefix)
    fake_id = Intake.model_fields["id"].sa_column.type.generate()

    response = client.get(api_app.url_path_for("get_last_question", intake_id=fake_id))
    assert_status(response, status.HTTP_404_NOT_FOUND)


def test_intake_expired(client: TestClient):
    doctor = DoctorFactory.build().save()
    appointment = AppointmentFactory.build(
        doctor_id=doctor.id,
        # create an appointment which already ended!
        appointment_date=AppointmentFactory.__faker__.date_between(
            start_date="-90d", end_date="today"
        ),
    ).save()

    intake = SimpleIncompleteIntakeFactory.build(
        appointment_id=appointment.id, doctor_id=doctor.id
    ).save()

    response = client.get(
        api_app.url_path_for("get_last_question", intake_id=intake.id)
    )

    assert_status(response, status.HTTP_410_GONE)


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
