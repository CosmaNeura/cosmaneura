from app.jobs.intake.generate_questions import perform

from app.models.intake.intake import Intake


# should not include PII in questions (idk how to test this)
def test_should_generate_intake_questions():
    intake = Intake.where(Intake.external_intake_data.contains({"firstName": "Coulter"})).one()
    assert len(intake.intake_questions) == 0
    assert intake.cleaned_external_intake_data is None

    perform(intake.id)

    intake = Intake.get(id=intake.id)
    questions = intake.intake_questions
    assert len(questions) > 0
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
