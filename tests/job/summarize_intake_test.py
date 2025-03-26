from app.jobs.summarize_intake import perform

from app.models.intake.intake import Intake

def test_should_generate_intake_summary():
    intake = Intake.select().one()
    assert not intake.summary

    perform(intake.id)

    intake = Intake.get(id=intake.id)
    assert intake.summary
