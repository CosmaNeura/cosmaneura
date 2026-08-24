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
    """An intake whose questions have not been generated yet reports "processing".

    This used to report "in_progress", which is what a *ready* question reports
    -- so nothing downstream could tell the two apart and the patient page's
    waiting screen was unreachable.
    """
    intake = SimpleIncompleteIntakeFactory.build().save()

    intake.intake_questions = []
    intake.save()

    assert intake.is_processing()
    assert not intake.is_complete()

    response = client.get(
        api_app.url_path_for("get_last_question", intake_id=intake.id)
    )

    assert_status(response)
    body = response.json()
    assert body["status"] == "processing"
    assert body["question"] is None


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
    body = response.json()
    # `question` is an object now, carrying the helper text, kind and choices the
    # patient page needs to render it.
    next_question = body["question"]
    assert next_question["question"]
    assert body["progress"] == {"answered": 0, "total": len(intake.intake_questions)}

    while next_question:
        answered_text = next_question["question"]
        answer = BaseFactory.__faker__.sentence()
        response = client.post(
            api_app.url_path_for("get_next_question", intake_id=intake.id),
            json={"answer": answer},
        )

        assert_status(response)
        response = response.json()

        intake.refresh()

        # Find the intake question we just answered
        intake_question = next(
            (q for q in intake.intake_questions if q.question == answered_text), None
        )
        assert intake_question
        assert intake_question.answer == answer
        assert intake_question.answer_timestamp is not None

        next_question = response["question"]

        if next_question:
            # A question is waiting. That is not the same state as questions
            # still being generated, so it is no longer reported as "processing".
            assert response["status"] == "in_progress"
            assert intake.summary is None
        else:
            assert response["status"] == "completed"
            assert len(intake.unanswered_questions()) == 0
            assert response["progress"] == {
                "answered": len(intake.intake_questions),
                "total": len(intake.intake_questions),
            }
            break

    intake.refresh()
    assert intake.summary
