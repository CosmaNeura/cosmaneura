"""The default Visit Prep question set.

Visit Prep replaces the model-generated intake interview with a short set of
questions the physician owns (CosmaNeura#135). The old flow asked
`app/jobs/intake/generate_questions.py` to invent an interview from the
doctor's bio and last clinical note; physicians did not trust a bot that could
surprise their patient, and the surprise is what the product now refuses to do.

Until there is a per-physician editor, every prep is created from the set
below. That is safe to change later because questions are *copied onto each
prep* at creation time (see `Intake.intake_questions`), so editing a default
never mutates a prep some patient is halfway through answering.

Question copy is deliberately physician-agnostic: the letter and the interview
page both name the physician around these questions, so the questions
themselves do not need to.
"""

from app.models.intake.intake import (
    IntakeSchemaQuestion,
    QuestionChoice,
    QuestionCondition,
)

# One number, not an architecture. `order` is just an integer, so any count
# works; this is a product judgment about how long a patient will keep typing
# on a phone, not a limit the model imposes.
MAX_QUESTIONS = 5

# Stable keys. Downstream logic references these rather than question text,
# which a physician can reword.
REASON = "reason"
MEDICATIONS = "medications"
ADHERENCE = "adherence"
ADHERENCE_BARRIER = "adherence_barrier"
STORY = "story"

# Answers to ADHERENCE that mean the regimen is not being taken as written, and
# therefore that ADHERENCE_BARRIER is worth asking. "none" is excluded on
# purpose: a patient on no medications has no barrier to describe.
ADHERENCE_IMPERFECT = ["some", "no"]


def default_questions() -> list[IntakeSchemaQuestion]:
    """A fresh copy of the default set.

    Returns new instances every call. These get mutated with answers once
    copied onto a prep, so a shared module-level list would leak one patient's
    answers into the next prep created in the same process.
    """
    return [
        IntakeSchemaQuestion(
            order=1,
            key=REASON,
            question="What is the main reason for this visit?",
            helper=(
                "In your own words. The thing you most want understood "
                "before you sit down."
            ),
            kind="story",
        ),
        IntakeSchemaQuestion(
            order=2,
            key=MEDICATIONS,
            question="What medications are you prescribed right now?",
            helper=(
                "Include the dose if you know it. Over-the-counter and "
                "supplements count too."
            ),
            kind="list",
        ),
        IntakeSchemaQuestion(
            order=3,
            key=ADHERENCE,
            question="Are you taking them as prescribed?",
            helper="There is no wrong answer. Honesty here saves the visit.",
            kind="choice",
            choices=[
                QuestionChoice(
                    value="yes",
                    label="Yes, as prescribed",
                    hint="Most days, as written",
                ),
                QuestionChoice(
                    value="some",
                    label="Some of them",
                    hint="A few get skipped",
                ),
                QuestionChoice(
                    value="no",
                    label="Not really",
                    hint="Something got in the way",
                ),
                QuestionChoice(
                    value="none",
                    label="I don't take any",
                ),
            ],
        ),
        IntakeSchemaQuestion(
            order=4,
            key=ADHERENCE_BARRIER,
            question="What got in the way?",
            helper=(
                "Cost, side effects, a chaotic morning, forgetting, not being "
                "sure it helps. All of that is useful."
            ),
            kind="story",
            show_if=QuestionCondition(key=ADHERENCE, any_of=ADHERENCE_IMPERFECT),
        ),
        IntakeSchemaQuestion(
            order=5,
            key=STORY,
            question="Tell me your health story — whatever feels relevant.",
            helper=(
                "What has been happening in your body, and in your life, that "
                "belongs in this visit?"
            ),
            kind="story",
        ),
    ]


def is_physician_authored(intake) -> bool:
    """Whether this prep carries a physician-authored question set.

    Authored questions have keys; model-generated ones never do. This is what
    routes an intake down the Visit Prep brief path rather than the legacy prose
    summary -- and it is why legacy intakes keep their existing behavior
    untouched, including the external-intake-data precondition that a Visit Prep
    prep has no way to satisfy.
    """
    return any(question.key for question in intake.intake_questions)


def answered_questions(intake) -> list[tuple[str, str]]:
    """(question, answer) for everything the patient actually answered, in order.

    Skips are dropped rather than passed along as empty strings: an unanswered
    question is not a fact about the patient, and feeding "" to the model invites
    it to invent one.
    """
    return [
        (question.question, question.answer)
        for question in intake.visible_questions()
        if question.answer
    ]
