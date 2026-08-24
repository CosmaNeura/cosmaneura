"""The Visit Prep brief path in the intake summarizer.

Runs against the offline stand-in rather than a live model: in the test
environment `should_use_test_openai_fallback()` is unconditional, so these
neither depend on a third party nor spend Azure credits per push.
"""

import pytest

import app.jobs.intake.summarize as summarize
from app.jobs.intake.summarize import brief_to_markdown, perform
from app.services.visit_prep import ADHERENCE, default_questions

from app.models.intake.intake import IntakeSchemaQuestion
from app.models.intake.intake_summary import BriefFlag, IntakeSummary, VisitBrief

from tests.factories import SimpleIncompleteIntakeFactory


@pytest.fixture(autouse=True)
def _allow_doctor_access(monkeypatch):
    monkeypatch.setattr(summarize, "doctor_access_in_force", lambda _doctor: True)


def _answer(question: IntakeSchemaQuestion, value: str) -> None:
    question.answer = value
    question.answer_timestamp = 1


def _authored_intake(adherence: str = "some", *, skip_story: bool = False):
    """A physician-authored prep with the interview filled in.

    `cleaned_external_intake_data` stays None on purpose: a Visit Prep prep has
    no external intake payload, and the point of the routing is that it no
    longer needs one.
    """
    questions = default_questions()
    by_key = {question.key: question for question in questions}

    _answer(by_key["reason"], "Exhausted for about four months.")
    _answer(by_key["medications"], "Levothyroxine 75 mcg every morning.")
    _answer(by_key[ADHERENCE], adherence)
    if adherence in ("some", "no"):
        _answer(by_key["adherence_barrier"], "Mornings with the kids are chaos.")
    _answer(
        by_key["story"], "" if skip_story else "New job in January, longer commute."
    )

    return SimpleIncompleteIntakeFactory.build(
        intake_questions=questions,
        cleaned_external_intake_data=None,
    ).save()


# --------------------------------------------------------------------------
# routing
# --------------------------------------------------------------------------


def test_authored_prep_does_not_require_external_intake_data():
    """The regression this routing exists to prevent.

    The legacy path raises "intake data not prepared" when there is no cleaned
    external payload. A physician-authored prep can never have one, so every
    Visit Prep completion would have raised.
    """
    intake = _authored_intake()
    assert intake.cleaned_external_intake_data is None

    perform(intake.id)

    intake.refresh()
    assert intake.summary


def test_legacy_intake_still_requires_external_intake_data():
    "Model-generated intakes keep their existing precondition."
    intake = SimpleIncompleteIntakeFactory.build(
        cleaned_external_intake_data=None
    ).save()
    for question in intake.intake_questions:
        _answer(question, "an answer")
    intake.save()

    with pytest.raises(ValueError, match="intake data not prepared"):
        perform(intake.id)


def test_unanswered_prep_is_still_refused():
    intake = SimpleIncompleteIntakeFactory.build(
        intake_questions=default_questions(),
        cleaned_external_intake_data=None,
    ).save()

    with pytest.raises(ValueError, match="intake questions not answered"):
        perform(intake.id)


# --------------------------------------------------------------------------
# what gets written
# --------------------------------------------------------------------------


def test_a_structured_brief_is_persisted():
    intake = _authored_intake()

    perform(intake.id)

    stored = IntakeSummary.get(intake_id=intake.id)
    assert stored is not None
    brief = stored.summary
    assert brief.headline
    assert brief.bullets
    assert brief.decision_points
    assert stored.doctor_id == intake.doctor_id


def test_the_prose_summary_is_derived_from_the_brief():
    """`intake.summary` has to keep holding prose.

    Appointment.intake_status keys off it and patient_context feeds it to
    Session Prep, so the string form cannot become JSON.
    """
    intake = _authored_intake()

    perform(intake.id)

    intake.refresh()
    stored = IntakeSummary.get(intake_id=intake.id)
    assert stored is not None
    prose = intake.summary
    assert prose is not None
    assert prose == brief_to_markdown(stored.summary)
    assert prose.startswith(stored.summary.headline)


def test_a_completed_prep_reports_complete():
    intake = _authored_intake()

    perform(intake.id)

    intake.refresh()
    assert intake.is_complete()
    assert intake.summary is not None


def test_summarizing_twice_does_not_write_a_second_brief():
    intake = _authored_intake()

    perform(intake.id)
    perform(intake.id)

    intake.refresh()
    stored = IntakeSummary.where(IntakeSummary.intake_id == intake.id).all()
    assert len(list(stored)) == 1


# --------------------------------------------------------------------------
# flags
# --------------------------------------------------------------------------


def test_imperfect_adherence_raises_an_adherence_flag():
    intake = _authored_intake(adherence="no")

    perform(intake.id)

    stored = IntakeSummary.get(intake_id=intake.id)
    assert stored is not None
    assert [flag.kind for flag in stored.summary.flags] == ["adherence"]


def test_taking_as_prescribed_raises_no_adherence_flag():
    intake = _authored_intake(adherence="yes")

    perform(intake.id)

    stored = IntakeSummary.get(intake_id=intake.id)
    assert stored is not None
    assert "adherence" not in [flag.kind for flag in stored.summary.flags]


def test_taking_no_medications_raises_no_adherence_flag():
    "'I don't take any' is not non-adherence."
    intake = _authored_intake(adherence="none")

    perform(intake.id)

    stored = IntakeSummary.get(intake_id=intake.id)
    assert stored is not None
    assert "adherence" not in [flag.kind for flag in stored.summary.flags]


# --------------------------------------------------------------------------
# skipped answers must not become facts
# --------------------------------------------------------------------------


def test_a_skipped_question_is_not_passed_to_the_brief():
    """A skip is an absence, not an empty answer.

    Handing "" to the model as though it were a response invites it to invent
    something to fill the gap.
    """
    intake = _authored_intake(skip_story=True)

    perform(intake.id)

    stored = IntakeSummary.get(intake_id=intake.id)
    assert stored is not None
    story_question = next(
        question for question in intake.intake_questions if question.key == "story"
    )
    assert story_question.question not in " ".join(stored.summary.bullets)


# --------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------


def test_markdown_renders_every_section_present():
    brief = VisitBrief(
        headline="Fatigue with an untold medication change.",
        bullets=["Levothyroxine taken four mornings a week."],
        decision_points=["Name the stopped medication first."],
        flags=[
            BriefFlag(
                kind="adherence",
                label="Levothyroxine gap",
                detail="Missed roughly three mornings a week.",
            )
        ],
        narrative="She is not here for a refill.",
    )

    rendered = brief_to_markdown(brief)

    assert rendered.startswith("Fatigue with an untold medication change.")
    assert "- Levothyroxine taken four mornings a week." in rendered
    assert "**Decisions for the room**" in rendered
    assert "**Flags**" in rendered
    assert "Levothyroxine gap" in rendered
    assert rendered.endswith("She is not here for a refill.")


def test_markdown_omits_sections_with_nothing_in_them():
    brief = VisitBrief(
        headline="Routine follow-up.",
        bullets=["Blood pressure stable."],
        decision_points=[],
        flags=[],
        narrative="",
    )

    rendered = brief_to_markdown(brief)

    assert "**Decisions for the room**" not in rendered
    assert "**Flags**" not in rendered
    assert rendered.endswith("- Blood pressure stable.")
