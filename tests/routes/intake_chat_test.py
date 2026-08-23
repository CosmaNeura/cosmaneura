import pytest
from fastapi import status
from fastapi.testclient import TestClient
from polyfactory import BaseFactory
from typeid import TypeID

import app.commands.intake.chat
import app.jobs.intake.summarize
import app.routes.chat

from tests.factories import (
    EXAMPLE_INTAKE_QUESTIONS,
    AppointmentFactory,
    DoctorFactory,
    SimpleCompleteIntakeFactory,
    SimpleIncompleteIntakeFactory,
)
from tests.integration.server import api_app
from tests.routes.utils import assert_status


@pytest.fixture(autouse=True)
def _allow_doctor_access(monkeypatch):
    monkeypatch.setattr(app.routes.chat, "doctor_access_in_force", lambda _doctor: True)
    monkeypatch.setattr(
        app.jobs.intake.summarize,
        "doctor_access_in_force",
        lambda _doctor: True,
    )


def test_invalid_intake_uuid(client: TestClient):
    response = client.get(api_app.url_path_for("get_last_question", intake_id="BAD_ID"))

    assert_status(response, status.HTTP_404_NOT_FOUND)


def test_uuid_cannot_be_found(client: TestClient):
    fake_id = TypeID("intake")

    response = client.get(api_app.url_path_for("get_last_question", intake_id=fake_id))
    assert_status(response, status.HTTP_404_NOT_FOUND)


def test_revoked_doctor_makes_public_intake_unavailable(
    client: TestClient,
    monkeypatch,
):
    intake = SimpleIncompleteIntakeFactory.build().save()
    initial_questions = intake.intake_questions
    monkeypatch.setattr(
        app.routes.chat, "doctor_access_in_force", lambda _doctor: False
    )

    get_response = client.get(
        api_app.url_path_for("get_last_question", intake_id=intake.id)
    )
    post_response = client.post(
        api_app.url_path_for("get_next_question", intake_id=intake.id),
        json={"answer": "private answer must not be persisted"},
    )

    assert get_response.status_code == status.HTTP_404_NOT_FOUND
    assert post_response.status_code == status.HTTP_404_NOT_FOUND
    intake.refresh()
    assert intake.intake_questions == initial_questions
    assert intake.summary is None


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

    assert_status(response)

    assert response.json()["status"] == "completed"


def test_intake_completed(client: TestClient):
    intake = SimpleCompleteIntakeFactory.build().save()

    assert not intake.is_processing()
    assert intake.is_complete()

    response = client.get(
        api_app.url_path_for("get_last_question", intake_id=intake.id)
    )

    assert_status(response)
    assert response.json()["status"] == "completed"


def test_intake_processing(client: TestClient):
    intake = SimpleIncompleteIntakeFactory.build().save()

    intake.intake_questions = []
    intake.save()

    assert intake.is_processing()
    assert not intake.is_complete()

    response = client.get(
        api_app.url_path_for("get_last_question", intake_id=intake.id)
    )

    assert_status(response)
    assert response.json()["status"] == "in_progress"


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
    assert result.order == EXAMPLE_INTAKE_QUESTIONS[0].order


def test_next_question():
    intake = SimpleIncompleteIntakeFactory.build().save()

    # should be empty to start
    assert not [q for q in intake.intake_questions if q.answer]

    # send in a fake response
    fake_response = BaseFactory.__faker__.sentence()
    result = app.commands.intake.chat.perform(intake, fake_response)

    assert result
    # second q test
    assert result.question == EXAMPLE_INTAKE_QUESTIONS[1].question
    assert result.order == EXAMPLE_INTAKE_QUESTIONS[1].order

    intake.refresh()

    # now we should have an answer
    answered_questions = [q for q in intake.intake_questions if q.answer]
    assert len(answered_questions) == 1
    assert answered_questions[0].order == EXAMPLE_INTAKE_QUESTIONS[0].order
    assert answered_questions[0].answer == fake_response


def test_terminal_question(client: TestClient, sync_celery):
    "can we properly exhaust the questions and kick off the intake summary?"

    intake = SimpleIncompleteIntakeFactory.build().save()

    response = client.get(
        api_app.url_path_for("get_last_question", intake_id=intake.id)
    )
    assert_status(response)
    next_question = response.json()["question"]

    while next_question:
        answer = BaseFactory.__faker__.sentence()
        response = client.post(
            api_app.url_path_for("get_next_question", intake_id=intake.id),
            json={"answer": answer},
        )

        assert_status(response)
        response = response.json()

        intake.refresh()

        # Find the intake question that matches next_question
        intake_question = next(
            (q for q in intake.intake_questions if q.question == next_question), None
        )
        assert intake_question
        assert intake_question.answer == answer
        assert intake_question.answer_timestamp is not None

        next_question = response["question"]

        if next_question:
            assert response["status"] == "processing"
            assert intake.summary is None
        else:
            assert response["status"] == "completed"
            assert len(intake.unanswered_questions()) == 0
            break

    intake.refresh()
    assert intake.summary
