from app.jobs.generate_intake_questions import perform

from app.models.intake.intake import Intake

# should be idempotent
# should not include PII in questions (idk how to test this)
# should save questions to the model
def test_should_generate_intake_questions():
    intake = Intake.select().one()
    assert len(intake.questions) == 0

    perform(intake.id)

    intake = Intake.get(id=intake.id)
    questions = intake.questions
    assert len(questions) > 0

    # Should be idempotent.
    perform(intake.id)

    intake = Intake.get(id=intake.id)
    new_questions = intake.questions
    assert questions == new_questions
