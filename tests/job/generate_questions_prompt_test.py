"""
Prompt-construction tests for intake question generation (no LLM involved).
"""

from app.jobs.intake.generate_questions import generate_questions_prompt

from app.models.doctor import Doctor

from tests.factories import DoctorFactory, SimpleIncompleteIntakeFactory

AGGREGATE_INSIGHTS = "Mostly retired adults; prefer plain language and short questions."


def test_population_insights_included_when_set():
    doctor = DoctorFactory.build(population_insights=AGGREGATE_INSIGHTS).save()
    intake = SimpleIncompleteIntakeFactory.build(doctor_id=doctor.id).save()

    prompt = generate_questions_prompt(intake, doctor)

    assert AGGREGATE_INSIGHTS in prompt
    assert "<POPULATION>" in prompt
    # the guardrail travels with the data
    assert (
        "Do NOT assume any POPULATION attribute is true of this specific patient"
        in prompt
    )


def test_population_section_absent_when_unset():
    doctor = DoctorFactory.build(population_insights=None).save()
    intake = SimpleIncompleteIntakeFactory.build(doctor_id=doctor.id).save()

    prompt = generate_questions_prompt(intake, doctor)

    assert "<POPULATION>" not in prompt


def test_population_insights_never_touch_provider_pinned_questions():
    """
    Provider-defined questions are used verbatim: population insights shape only
    AI-generated questions, so pinned questions must come out unchanged.
    """
    from app.jobs.intake.generate_questions import perform

    pinned = ["What brings you in today?", "Any changes to your medications?"]
    doctor: Doctor = DoctorFactory.build(
        population_insights=AGGREGATE_INSIGHTS,
        custom_intake_questions=pinned,
    ).save()
    intake = SimpleIncompleteIntakeFactory.build(
        doctor_id=doctor.id, intake_questions=[]
    ).save()

    perform(intake.id)

    intake.refresh()
    assert [q.question for q in intake.intake_questions] == pinned
