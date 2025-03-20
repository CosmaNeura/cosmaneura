
from fastapi.testclient import TestClient
from polyfactory import BaseFactory

import app.commands.intake.chat

from tests.factories import EXAMPLE_INTAKE_QUESTIONS, SimpleIncompleteIntakeFactory


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
