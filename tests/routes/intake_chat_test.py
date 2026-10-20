from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import Mock

import pytest
from fastapi import status
from fastapi.testclient import TestClient
from polyfactory import BaseFactory
from typeid import TypeID

import app.commands.intake.chat
import app.jobs.intake.summarize
import app.routes.chat

from activemodel.session_manager import SessionManager
from app.models.doctor import Doctor
from app.models.superadmin_capability import (
    SuperadminCapabilityEnvironment,
    SuperadminCapabilityKind,
)

from tests.factories import (
    EXAMPLE_INTAKE_QUESTIONS,
    AppointmentFactory,
    DoctorFactory,
    SimpleCompleteIntakeFactory,
    SimpleIncompleteIntakeFactory,
)
from tests.integration.server import api_app
from tests.routes.utils import (
    MOCK_CLERK_ISSUER,
    assert_status,
    get_clerk_dev_user,
    grant_test_superadmin_capability,
    provision_practice_in_force,
)


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


def test_active_view_as_cookie_blocks_cross_origin_public_intake_write(
    authenticated_client: TestClient,
    monkeypatch,
):
    def staging_environment():
        return SuperadminCapabilityEnvironment.staging

    for path in (
        "app.routes.dependencies.view_as.configured_superadmin_capability_environment",
        "app.routes.view_as.configured_superadmin_capability_environment",
        "app.services.superadmin_capability.configured_superadmin_capability_environment",
    ):
        monkeypatch.setattr(path, staging_environment)
    monkeypatch.setattr(
        "app.services.superadmin_capability.configured_superadmin_clerk_issuer",
        lambda: MOCK_CLERK_ISSUER,
    )

    def current_email(clerk_id: str) -> str | None:
        doctor = Doctor.get(clerk_id=clerk_id)
        return doctor.email.strip().lower() if doctor and doctor.email else None

    monkeypatch.setattr(
        "app.services.view_as.verified_primary_email",
        current_email,
    )

    _, _, clerk_user = get_clerk_dev_user()
    actor = Doctor.get(clerk_id=clerk_user.id)
    assert actor is not None
    grant_test_superadmin_capability(actor, SuperadminCapabilityKind.view_as)

    target = Doctor(
        clerk_id="user_public_intake_view_as_target",
        email="public-intake-view-as-target@example.net",
        biography="",
    ).save()
    provision_practice_in_force(target)
    intake = SimpleIncompleteIntakeFactory.build(doctor_id=target.id).save()
    original_questions = [
        question.model_copy(deep=True) for question in intake.intake_questions
    ]

    began = authenticated_client.post(
        api_app.url_path_for("begin_view_as"),
        json={"target_doctor_id": str(target.id)},
    )
    assert began.status_code == status.HTTP_200_OK, began.text

    queued = Mock()
    monkeypatch.setattr(app.commands.intake.chat, "queue_intake_summarization", queued)
    response = authenticated_client.post(
        api_app.url_path_for("get_next_question", intake_id=intake.id),
        headers={"Origin": "https://chat.cosma.localhost"},
        json={"answer": "must not be persisted"},
    )

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert response.headers["access-control-allow-origin"] == (
        "https://chat.cosma.localhost"
    )
    intake.refresh()
    assert intake.intake_questions == original_questions
    assert intake.summary is None
    queued.assert_not_called()


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


def test_bound_retry_never_answers_the_following_question(client: TestClient):
    intake = SimpleIncompleteIntakeFactory.build().save()
    endpoint = api_app.url_path_for("get_next_question", intake_id=intake.id)
    payload = {
        "answer": "Synthetic first answer",
        "expected_question": EXAMPLE_INTAKE_QUESTIONS[0].question,
    }
    initial = client.post(endpoint, json=payload)
    retried = client.post(endpoint, json=payload)
    assert initial.status_code == retried.status_code == 200
    assert initial.json() == retried.json()
    assert retried.json()["retry_safe"] is True
    intake.refresh()
    assert intake.intake_questions[0].answer == payload["answer"]
    assert intake.intake_questions[1].answer is None


def test_bound_conflicting_answer_cannot_overwrite_or_advance(client: TestClient):
    intake = SimpleIncompleteIntakeFactory.build().save()
    endpoint = api_app.url_path_for("get_next_question", intake_id=intake.id)
    question = EXAMPLE_INTAKE_QUESTIONS[0].question
    assert (
        client.post(
            endpoint, json={"answer": "Synthetic answer", "expected_question": question}
        ).status_code
        == 200
    )
    response = client.post(
        endpoint, json={"answer": "Different answer", "expected_question": question}
    )
    assert response.status_code == 409
    intake.refresh()
    assert intake.intake_questions[0].answer == "Synthetic answer"
    assert intake.intake_questions[1].answer is None


def test_concurrent_bound_replay_refreshes_after_row_lock(
    client: TestClient, monkeypatch
):
    # Concurrent requests need distinct connections, not the rollback fixture's
    # shared connection. These synthetic rows live only in the disposable DB.
    monkeypatch.setattr(SessionManager.get_instance(), "session_connection", None)
    intake = SimpleIncompleteIntakeFactory.build().save()
    endpoint = api_app.url_path_for("get_next_question", intake_id=intake.id)
    payload = {
        "answer": "Synthetic concurrent answer",
        "expected_question": EXAMPLE_INTAKE_QUESTIONS[0].question,
    }
    entered, release, second_loaded = Event(), Event(), Event()
    original = app.commands.intake.chat.perform
    original_process = app.routes.chat.process_intake

    def observe_process(intake_id: str):
        result = original_process(intake_id)
        if entered.is_set():
            second_loaded.set()
        return result

    def held_perform(*args, **kwargs):
        if not entered.is_set():
            entered.set()
            assert release.wait(5)
        return original(*args, **kwargs)

    monkeypatch.setattr(app.commands.intake.chat, "perform", held_perform)
    api_app.dependency_overrides[original_process] = observe_process
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(client.post, endpoint, json=payload)
            assert entered.wait(5)
            second = pool.submit(client.post, endpoint, json=payload)
            assert second_loaded.wait(5)
            release.set()
            responses = [first.result(timeout=10), second.result(timeout=10)]
    finally:
        release.set()
        api_app.dependency_overrides.pop(original_process, None)
    assert all(response.status_code == 200 for response in responses)
    assert responses[0].json() == responses[1].json()
    intake.refresh()
    assert intake.intake_questions[0].answer == payload["answer"]
    assert intake.intake_questions[1].answer is None


def test_bound_unknown_question_does_not_write(client: TestClient):
    intake = SimpleIncompleteIntakeFactory.build().save()
    response = client.post(
        api_app.url_path_for("get_next_question", intake_id=intake.id),
        json={
            "answer": "Synthetic answer",
            "expected_question": "Unknown synthetic question",
        },
    )
    assert response.status_code == 409
    intake.refresh()
    assert all(question.answer is None for question in intake.intake_questions)


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
