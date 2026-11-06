"""Publication rejects ambiguous question flows and preserves a free-form story."""

from dataclasses import replace

import pytest

from app.services.visit_prep_question_definitions import (
    CANONICAL_DEFAULT_QUESTION_SET,
    QuestionChoiceInput,
    QuestionConditionInput,
    QuestionSetVersionInput,
    VisitPrepQuestionSetValidation,
    validate_question_set_draft,
    validate_question_set_for_publish,
)


@pytest.mark.parametrize(
    ("definition", "message"),
    (
        (
            QuestionSetVersionInput(
                name="Too short",
                questions=CANONICAL_DEFAULT_QUESTION_SET.questions[:4],
            ),
            "question count is invalid",
        ),
        (
            replace(
                CANONICAL_DEFAULT_QUESTION_SET,
                questions=(
                    CANONICAL_DEFAULT_QUESTION_SET.questions[0],
                    replace(
                        CANONICAL_DEFAULT_QUESTION_SET.questions[1],
                        key=CANONICAL_DEFAULT_QUESTION_SET.questions[0].key,
                    ),
                    *CANONICAL_DEFAULT_QUESTION_SET.questions[2:],
                ),
            ),
            "question keys must be unique",
        ),
        (
            replace(
                CANONICAL_DEFAULT_QUESTION_SET,
                questions=(
                    replace(
                        CANONICAL_DEFAULT_QUESTION_SET.questions[0],
                        show_if=QuestionConditionInput(
                            question_key="adherence",
                            values=("no",),
                        ),
                    ),
                    *CANONICAL_DEFAULT_QUESTION_SET.questions[1:],
                ),
            ),
            "question condition must target an earlier choice question",
        ),
        (
            replace(
                CANONICAL_DEFAULT_QUESTION_SET,
                questions=(
                    *CANONICAL_DEFAULT_QUESTION_SET.questions[:3],
                    replace(
                        CANONICAL_DEFAULT_QUESTION_SET.questions[3],
                        show_if=QuestionConditionInput(
                            question_key="adherence",
                            values=("unknown",),
                        ),
                    ),
                    CANONICAL_DEFAULT_QUESTION_SET.questions[4],
                ),
            ),
            "question condition value is not a target choice",
        ),
        (
            replace(
                CANONICAL_DEFAULT_QUESTION_SET,
                questions=(
                    *CANONICAL_DEFAULT_QUESTION_SET.questions[:2],
                    replace(
                        CANONICAL_DEFAULT_QUESTION_SET.questions[2],
                        choices=(
                            QuestionChoiceInput(value="yes", label="Yes"),
                            QuestionChoiceInput(value="yes", label="Also yes"),
                        ),
                    ),
                    *CANONICAL_DEFAULT_QUESTION_SET.questions[3:],
                ),
            ),
            "question choice values must be unique",
        ),
        (
            replace(
                CANONICAL_DEFAULT_QUESTION_SET,
                questions=(
                    *CANONICAL_DEFAULT_QUESTION_SET.questions[:2],
                    replace(
                        CANONICAL_DEFAULT_QUESTION_SET.questions[2],
                        choices=(
                            QuestionChoiceInput(value="yes", label=" Same label "),
                            QuestionChoiceInput(value="no", label="Same label"),
                        ),
                    ),
                    *CANONICAL_DEFAULT_QUESTION_SET.questions[3:],
                ),
            ),
            "question choice labels must be unique",
        ),
    ),
)
def test_publish_validation_rejects_ambiguous_or_unreachable_flows(
    definition: QuestionSetVersionInput,
    message: str,
) -> None:
    with pytest.raises(VisitPrepQuestionSetValidation, match=message):
        validate_question_set_for_publish(definition)


def _with_narrative(**changes):
    original = CANONICAL_DEFAULT_QUESTION_SET
    return replace(
        original,
        questions=(
            *original.questions[:-1],
            replace(original.questions[-1], **changes),
        ),
    )


@pytest.mark.parametrize(
    "changes",
    (
        {"kind": "list"},
        {
            "kind": "choice",
            "choices": (
                QuestionChoiceInput("yes", "Yes"),
                QuestionChoiceInput("no", "No"),
            ),
        },
        {"show_if": QuestionConditionInput("adherence", ("no",))},
    ),
)
def test_publish_requires_a_free_form_narrative_even_when_draft_is_valid(changes):
    definition = _with_narrative(**changes)
    assert validate_question_set_draft(definition) == definition
    with pytest.raises(
        VisitPrepQuestionSetValidation, match="unconditional free-form story"
    ):
        validate_question_set_for_publish(definition)
