"""
Provider-defined questions take precedence over LLM question generation.

These tests run fully offline: with provider questions set, the generation job
never needs an LLM; without them, the offline test fallback generates questions.
"""

from app.constants import MAX_INTAKE_QUESTIONS
from app.jobs.intake.generate_questions import perform

from app.models.doctor import Doctor
from app.models.intake.intake import Intake

from tests.factories import DoctorFactory, SimpleIncompleteIntakeFactory

PROVIDER_QUESTIONS = [
    "What brings you in today?",
    "What medications are you currently taking?",
    "Have you had any recent hospital visits?",
]


def _intake_for(doctor: Doctor) -> Intake:
    return SimpleIncompleteIntakeFactory.build(
        doctor_id=doctor.id, intake_questions=[]
    ).save()


def test_provider_questions_used_verbatim():
    doctor = DoctorFactory.build(custom_intake_questions=PROVIDER_QUESTIONS).save()
    intake = _intake_for(doctor)

    perform(intake.id)

    intake.refresh()
    assert [q.question for q in intake.intake_questions] == PROVIDER_QUESTIONS
    assert [q.order for q in intake.intake_questions] == [0, 1, 2]


def test_provider_questions_capped_at_max():
    too_many = [f"Provider question {n}?" for n in range(MAX_INTAKE_QUESTIONS + 2)]
    doctor = DoctorFactory.build(custom_intake_questions=too_many).save()
    intake = _intake_for(doctor)

    perform(intake.id)

    intake.refresh()
    assert len(intake.intake_questions) == MAX_INTAKE_QUESTIONS
    assert [q.question for q in intake.intake_questions] == too_many[
        :MAX_INTAKE_QUESTIONS
    ]


def test_generated_questions_used_when_no_provider_questions():
    doctor = DoctorFactory.build(custom_intake_questions=None).save()
    intake = _intake_for(doctor)

    perform(intake.id)

    intake.refresh()
    assert len(intake.intake_questions) >= 1
    assert len(intake.intake_questions) <= MAX_INTAKE_QUESTIONS
    assert [q.question for q in intake.intake_questions] != PROVIDER_QUESTIONS
