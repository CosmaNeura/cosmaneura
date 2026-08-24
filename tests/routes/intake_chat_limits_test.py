"""Input bounds on the unauthenticated patient interview endpoint.

Possession of the link is the only credential here, so anyone holding a
forwarded one can write to the record. Every byte they send lands in a
PHI-bearing JSONB column and then in a model prompt.
"""

import pytest
from fastapi import status
from fastapi.testclient import TestClient

import app.commands.intake.chat
import app.routes.chat
from app.routes.chat import MAX_ANSWER_CHARS, MAX_CHAT_BODY_BYTES

from app.models.intake.intake import IntakeSchemaQuestion, QuestionCondition

from tests.factories import SimpleIncompleteIntakeFactory
from tests.integration.server import api_app
from tests.routes.utils import assert_status


@pytest.fixture(autouse=True)
def _allow_doctor_access(monkeypatch):
    monkeypatch.setattr(app.routes.chat, "doctor_access_in_force", lambda _doctor: True)


def _post(client: TestClient, intake, answer: str):
    return client.post(
        api_app.url_path_for("get_next_question", intake_id=intake.id),
        json={"answer": answer},
    )


def test_an_answer_within_the_limit_is_accepted(client: TestClient):
    intake = SimpleIncompleteIntakeFactory.build().save()

    response = _post(client, intake, "A" * (MAX_ANSWER_CHARS - 1))

    assert_status(response)


def test_an_oversized_answer_is_refused_and_not_stored(client: TestClient):
    intake = SimpleIncompleteIntakeFactory.build().save()

    response = _post(client, intake, "A" * (MAX_ANSWER_CHARS + 1))

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    intake.refresh()
    assert all(question.answer is None for question in intake.intake_questions)


def test_a_body_beyond_the_cap_is_refused_before_it_is_parsed(client: TestClient):
    """Rejected on content-length, so a hostile body never reaches memory.

    The character limit is the binding one -- a caller can understate or omit
    content-length -- but this keeps the obvious case cheap.
    """
    intake = SimpleIncompleteIntakeFactory.build().save()

    response = _post(client, intake, "A" * (MAX_CHAT_BODY_BYTES + 1))

    assert response.status_code == status.HTTP_413_REQUEST_ENTITY_TOO_LARGE
    intake.refresh()
    assert all(question.answer is None for question in intake.intake_questions)


def test_a_skip_is_still_accepted(client: TestClient):
    "The bound must not break the empty-string skip."
    intake = SimpleIncompleteIntakeFactory.build().save()

    response = _post(client, intake, "")

    assert_status(response)
    intake.refresh()
    assert any(question.is_answered for question in intake.intake_questions)


# --------------------------------------------------------------------------
# condition graph
# --------------------------------------------------------------------------


def _questions(*specs):
    return [
        IntakeSchemaQuestion(
            order=order,
            question=f"Q{order}",
            key=key,
            show_if=QuestionCondition(key=gate, any_of=["x"]) if gate else None,
        )
        for order, key, gate in specs
    ]


def test_a_condition_on_an_earlier_question_is_accepted():
    intake = SimpleIncompleteIntakeFactory.build(
        intake_questions=_questions((1, "a", None), (2, "b", "a"))
    ).save()

    app.commands.intake.chat.validate_question_structure(intake)


@pytest.mark.parametrize(
    "specs,description",
    [
        ([(1, "a", "b"), (2, "b", "a")], "a two-question cycle"),
        ([(1, "a", "a")], "a self-reference"),
        ([(1, "a", "b"), (2, "b", None)], "a forward reference"),
    ],
)
def test_an_unreachable_condition_is_refused(specs, description):
    """Every question in a cycle is permanently invisible.

    Which is worse than it sounds: the prep then reports itself complete having
    asked nothing, and a brief gets written from no answers at all.
    """
    intake = SimpleIncompleteIntakeFactory.build(
        intake_questions=_questions(*specs)
    ).save()

    with pytest.raises(ValueError, match="not asked before it"):
        app.commands.intake.chat.validate_question_structure(intake)


def test_a_cycle_would_otherwise_complete_having_asked_nothing():
    "The behavior the validation above exists to prevent."
    intake = SimpleIncompleteIntakeFactory.build(
        intake_questions=_questions((1, "a", "b"), (2, "b", "a"))
    ).save()

    assert intake.visible_questions() == []
    assert intake.is_complete() is True
