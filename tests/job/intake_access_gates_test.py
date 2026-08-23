"""Execution-time authorization checks for asynchronous intake PHI work."""

from app.jobs.intake import generate_questions, summarize

from app.models.intake.intake import IntakeSchemaQuestion

from tests.factories import SimpleIncompleteIntakeFactory


def _unexpected(*_args, **_kwargs):
    raise AssertionError("intake PHI processing must not run after access denial")


def test_question_generation_denial_skips_cleaning_and_model_calls(monkeypatch):
    intake = SimpleIncompleteIntakeFactory.build(
        intake_questions=[],
        cleaned_external_intake_data=None,
    ).save()
    monkeypatch.setattr(
        generate_questions, "doctor_access_in_force", lambda _doctor: False
    )
    monkeypatch.setattr(generate_questions, "extract_intake_data", _unexpected)
    monkeypatch.setattr(generate_questions, "generate_intake_questions", _unexpected)

    generate_questions.perform(intake.id)

    intake.refresh()
    assert intake.cleaned_external_intake_data is None
    assert intake.intake_questions == []


def test_intake_summarization_denial_skips_model_and_write(monkeypatch):
    answered = IntakeSchemaQuestion(
        order=1,
        question="Private question sentinel",
        required_details="Private required details sentinel",
        answer="Private answer sentinel",
    )
    intake = SimpleIncompleteIntakeFactory.build(
        intake_questions=[answered],
        summary=None,
    ).save()
    monkeypatch.setattr(summarize, "doctor_access_in_force", lambda _doctor: False)
    monkeypatch.setattr(summarize, "generate_intake_summary", _unexpected)

    summarize.perform(intake.id)

    intake.refresh()
    assert intake.summary is None
