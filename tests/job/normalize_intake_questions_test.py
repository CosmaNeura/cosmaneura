from app.constants import MAX_INTAKE_QUESTIONS
from app.jobs.intake.generate_questions import normalize_questions

from app.models.intake.intake import IntakeSchemaQuestion


def build_question(order: int, question: str) -> IntakeSchemaQuestion:
    return IntakeSchemaQuestion(
        order=order, question=question, required_details="details"
    )


def test_valid_questions_pass_through():
    questions = [
        build_question(2, "What medications are you taking?"),
        build_question(1, "How long have you had symptoms?"),
    ]

    normalized = normalize_questions(questions)

    assert [q.question for q in normalized] == [
        "How long have you had symptoms?",
        "What medications are you taking?",
    ]
    assert [q.order for q in normalized] == [0, 1]


def test_drops_duplicate_question_text():
    questions = [
        build_question(1, "How long have you had symptoms?"),
        # duplicates differing only in case/whitespace are still duplicates
        build_question(2, "  how long have you had symptoms? "),
        build_question(3, "What medications are you taking?"),
    ]

    normalized = normalize_questions(questions)

    assert [q.question for q in normalized] == [
        "How long have you had symptoms?",
        "What medications are you taking?",
    ]
    assert [q.order for q in normalized] == [0, 1]


def test_reassigns_duplicate_orders():
    questions = [
        build_question(1, "Question A"),
        build_question(1, "Question B"),
        build_question(0, "Question C"),
    ]

    normalized = normalize_questions(questions)

    # stable sort: ties keep their original relative ordering
    assert [q.question for q in normalized] == [
        "Question C",
        "Question A",
        "Question B",
    ]
    assert [q.order for q in normalized] == [0, 1, 2]


def test_caps_question_count():
    questions = [
        build_question(order, f"Question {order}?")
        for order in range(MAX_INTAKE_QUESTIONS + 3)
    ]

    normalized = normalize_questions(questions)

    # the earliest questions win
    assert len(normalized) == MAX_INTAKE_QUESTIONS
    assert [q.question for q in normalized] == [
        f"Question {order}?" for order in range(MAX_INTAKE_QUESTIONS)
    ]
