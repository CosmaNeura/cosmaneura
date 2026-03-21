# pyright: reportAttributeAccessIssue=false, reportOptionalMemberAccess=false, reportArgumentType=false

import os

import pytest

from app.jobs.intake.generate_questions import generate_intake_questions, perform
from app.models.doctor import Doctor
from app.models.intake.intake import Intake


_RUN_LIVE_LLM_TESTS = os.getenv("RUN_LIVE_LLM_TESTS") == "true"
_HAS_AZURE_OPENAI_CREDS = bool(
    os.getenv("AZURE_OPENAI_API_KEY")
    and os.getenv("AZURE_OPENAI_ENDPOINT")
)
pytestmark = pytest.mark.skipif(
    not (_RUN_LIVE_LLM_TESTS and _HAS_AZURE_OPENAI_CREDS),
    reason=(
        "requires live Azure OpenAI access; set RUN_LIVE_LLM_TESTS=true and Azure OpenAI env vars"
    ),
)


# should not include PII in questions (idk how to test this)
def test_should_generate_intake_questions():
    intake = Intake.where(
        Intake.external_intake_data.contains({"firstName": "Coulter"})
    ).one()
    assert len(intake.intake_questions) == 0
    assert intake.cleaned_external_intake_data is None

    perform(intake.id)

    intake = Intake.get(id=intake.id)
    questions = intake.intake_questions
    assert len(questions) >= 3
    assert len(questions) <= 10
    cleaned_data = intake.cleaned_external_intake_data
    assert cleaned_data
    assert len(cleaned_data.reason_for_visit) > 0
    assert len(cleaned_data.appointment_type) > 0

    # Should be idempotent.
    perform(intake.id)

    intake = Intake.get(id=intake.id)
    new_questions = intake.intake_questions
    assert questions == new_questions
    assert cleaned_data == intake.cleaned_external_intake_data


def test_should_vary_question_count():
    intake = Intake.where(
        Intake.cleaned_external_intake_data.contains(
            {"reason_for_visit": "Diabetes management"}
        )
    ).one()
    doctor = Doctor.one(id=intake.doctor_id)

    # Simple intake.
    data = intake.cleaned_external_intake_data
    data.reason_for_visit = "Seasonal influenza vaccination"
    data.appointment_type = "Nurse visit – immunization"
    data.additional_info = "No allergies to vaccines or components; received last flu shot 12 months ago; no current illness"
    intake.cleaned_external_intake_data = data
    intake.flag_modified("cleaned_external_intake_data")
    intake.save()

    simple_questions = []
    for _ in range(5):
        simple_questions += generate_intake_questions(intake, doctor)

    # Complex intake.
    data = intake.cleaned_external_intake_data
    data.reason_for_visit = "Sudden episodes of palpitations, dizziness, and blurred vision while exercising; chest tightness when lying flat"
    data.appointment_type = "New patient – cardiology referral"
    data.additional_info = "Uses over-the-counter weight-loss supplements and energy drinks; syncopal event during a 10K run last week; personal history of repaired congenital heart defect; Apple Watch flagged an irregular rhythm"
    intake.cleaned_external_intake_data = data
    intake.flag_modified("cleaned_external_intake_data")
    intake.save()

    complex_questions = []
    for _ in range(5):
        complex_questions += generate_intake_questions(intake, doctor)

    # assert len(simple_questions) < len(complex_questions)
    # print(
    #     f"simple questions: {len(simple_questions)}, complex questions: {len(complex_questions)}"
    # )
