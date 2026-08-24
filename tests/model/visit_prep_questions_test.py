"""Conditional-question traversal and the default Visit Prep set.

Pure-model tests: the `Intake` instances are never saved, because every method
under test reads only `self.intake_questions`.
"""

import pytest

from app.services.visit_prep import (
    ADHERENCE,
    ADHERENCE_BARRIER,
    MAX_QUESTIONS,
    MEDICATIONS,
    REASON,
    STORY,
    default_questions,
)

from app.models.intake.intake import (
    Intake,
    IntakeSchemaQuestion,
    QuestionChoice,
    QuestionCondition,
)


def _intake(questions: list[IntakeSchemaQuestion]) -> Intake:
    """An unsaved Intake carrying just enough to exercise question traversal.

    Constructed normally rather than with `model_construct`: these are
    SQLAlchemy-instrumented attributes, and skipping `__init__` leaves them
    unset so even reading `intake_questions` raises. The foreign keys are
    omitted because nothing here is saved and no method under test reads them.
    """
    return Intake(external_intake_data={}, intake_questions=questions)


def _answer(question: IntakeSchemaQuestion, value: str) -> None:
    question.answer = value
    question.answer_timestamp = 1


# --------------------------------------------------------------------------
# is_answered
# --------------------------------------------------------------------------


def test_unanswered_question_is_not_answered():
    question = IntakeSchemaQuestion(order=1, question="Why are you here?")
    assert question.is_answered is False


def test_skipped_question_counts_as_answered():
    """A skip stores an empty string.

    `if not answer` would treat that as unanswered and the interview would
    serve the same question forever, so `is_answered` keys off the timestamp.
    """
    question = IntakeSchemaQuestion(order=1, question="Why are you here?")
    _answer(question, "")

    assert question.answer == ""
    assert question.is_answered is True


# --------------------------------------------------------------------------
# conditions
# --------------------------------------------------------------------------


def test_condition_matches_listed_value():
    condition = QuestionCondition(key=ADHERENCE, any_of=["some", "no"])

    assert condition.satisfied_by({ADHERENCE: "no"}) is True
    assert condition.satisfied_by({ADHERENCE: "some"}) is True


def test_condition_rejects_unlisted_value():
    condition = QuestionCondition(key=ADHERENCE, any_of=["some", "no"])

    assert condition.satisfied_by({ADHERENCE: "yes"}) is False
    assert condition.satisfied_by({ADHERENCE: "none"}) is False


def test_condition_is_unsatisfied_when_the_gate_is_unanswered_or_skipped():
    "An unanswered or skipped gate means we do not know there is a problem."
    condition = QuestionCondition(key=ADHERENCE, any_of=["some", "no"])

    assert condition.satisfied_by({}) is False
    assert condition.satisfied_by({ADHERENCE: ""}) is False


# --------------------------------------------------------------------------
# traversal
# --------------------------------------------------------------------------


def test_model_generated_questions_are_all_visible():
    "No keys and no conditions: identical to the pre-Visit-Prep behavior."
    questions = [
        IntakeSchemaQuestion(order=2, question="Second"),
        IntakeSchemaQuestion(order=1, question="First"),
    ]
    intake = _intake(questions)

    assert [q.question for q in intake.visible_questions()] == ["First", "Second"]
    assert len(intake.unanswered_questions()) == 2

    next_question = intake.next_unanswered_question()
    assert next_question is not None
    assert next_question.question == "First"


def test_conditional_question_is_hidden_until_its_gate_matches():
    questions = default_questions()
    intake = _intake(questions)
    by_key = {q.key: q for q in questions}

    # Before the gate is answered the follow-up is not in play at all.
    assert ADHERENCE_BARRIER not in [q.key for q in intake.visible_questions()]

    _answer(by_key[ADHERENCE], "no")

    assert ADHERENCE_BARRIER in [q.key for q in intake.visible_questions()]


def test_taking_everything_as_prescribed_still_completes():
    """The regression this whole mechanism exists to prevent.

    If a hidden question counted toward completion, a patient who takes their
    medication as written would answer four questions and never finish.
    """
    questions = default_questions()
    intake = _intake(questions)
    by_key = {q.key: q for q in questions}

    _answer(by_key[REASON], "Blood pressure follow-up")
    _answer(by_key[MEDICATIONS], "Lisinopril 20mg")
    _answer(by_key[ADHERENCE], "yes")
    _answer(by_key[STORY], "New job, more stress")

    assert intake.unanswered_questions() == []
    assert intake.next_unanswered_question() is None


def test_partial_adherence_requires_the_barrier_question():
    questions = default_questions()
    intake = _intake(questions)
    by_key = {q.key: q for q in questions}

    _answer(by_key[REASON], "Exhausted for months")
    _answer(by_key[MEDICATIONS], "Levothyroxine 75mcg")
    _answer(by_key[ADHERENCE], "some")
    _answer(by_key[STORY], "Caring for my mother")

    remaining = intake.unanswered_questions()
    assert [q.key for q in remaining] == [ADHERENCE_BARRIER]


def test_no_medications_does_not_ask_about_barriers():
    "'I don't take any' is not a barrier to describe."
    questions = default_questions()
    intake = _intake(questions)
    by_key = {q.key: q for q in questions}

    _answer(by_key[ADHERENCE], "none")

    assert ADHERENCE_BARRIER not in [q.key for q in intake.visible_questions()]


def test_visible_questions_are_ordered():
    questions = default_questions()
    intake = _intake(questions)

    orders = [q.order for q in intake.visible_questions()]
    assert orders == sorted(orders)


def test_skipping_the_gate_skips_the_follow_up():
    questions = default_questions()
    intake = _intake(questions)
    by_key = {q.key: q for q in questions}

    _answer(by_key[REASON], "")
    _answer(by_key[MEDICATIONS], "")
    _answer(by_key[ADHERENCE], "")
    _answer(by_key[STORY], "")

    assert intake.unanswered_questions() == []


# --------------------------------------------------------------------------
# the default set itself
# --------------------------------------------------------------------------


def test_default_set_is_within_the_cap():
    assert len(default_questions()) == MAX_QUESTIONS


def test_default_questions_are_fresh_instances():
    """Answers get written onto these objects once copied to a prep.

    A shared module-level list would carry one patient's answers into the next
    prep created in the same process.
    """
    first = default_questions()
    second = default_questions()

    first[0].answer = "leaked"

    assert second[0].answer is None
    assert first[0] is not second[0]


def test_every_default_question_is_keyed_and_helped():
    for question in default_questions():
        assert question.key, f"order {question.order} has no key"
        assert question.helper, f"{question.key} has no helper text"


def test_default_keys_are_unique():
    keys = [q.key for q in default_questions()]
    assert len(keys) == len(set(keys))


def test_only_the_adherence_question_offers_choices():
    for question in default_questions():
        if question.key == ADHERENCE:
            assert question.kind == "choice"
            assert question.choices
        else:
            assert question.choices is None


def test_adherence_choices_cover_every_condition_value():
    "A condition value with no matching choice could never be selected."
    adherence = next(q for q in default_questions() if q.key == ADHERENCE)
    barrier = next(q for q in default_questions() if q.key == ADHERENCE_BARRIER)
    assert barrier.show_if is not None

    offered = {choice.value for choice in adherence.choices or []}
    assert set(barrier.show_if.any_of) <= offered


def test_every_condition_points_at_a_real_key():
    questions = default_questions()
    keys = {q.key for q in questions if q.key}

    for question in questions:
        if question.show_if:
            assert question.show_if.key in keys


@pytest.mark.parametrize("kind", ["story", "list", "choice"])
def test_question_kinds_are_accepted(kind):
    question = IntakeSchemaQuestion(
        order=1,
        question="Anything?",
        kind=kind,
        choices=[QuestionChoice(value="y", label="Yes")] if kind == "choice" else None,
    )
    assert question.kind == kind


def test_required_details_is_optional_for_authored_questions():
    "Model-generated questions supply it; physician-authored ones have no use for it."
    question = IntakeSchemaQuestion(order=1, question="Why are you here?")
    assert question.required_details == ""


def test_an_answer_without_a_timestamp_still_counts_as_answered():
    """Older rows, backfills and fixtures can carry an answer and no timestamp.

    Requiring the timestamp would quietly revert those to unanswered, and their
    preps would never complete.
    """
    question = IntakeSchemaQuestion(order=1, question="Why are you here?")
    question.answer = "Blood pressure follow-up"

    assert question.answer_timestamp is None
    assert question.is_answered is True


def test_a_timestamp_without_an_answer_still_counts_as_answered():
    question = IntakeSchemaQuestion(order=1, question="Why are you here?")
    question.answer_timestamp = 1

    assert question.is_answered is True
