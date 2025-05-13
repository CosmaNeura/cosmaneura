from app.jobs.intake.summarize import perform

from app.models.intake.intake import ExternalIntakeData, Intake, IntakeSchemaQuestion


def test_should_generate_intake_summary():
    intake = Intake.where(Intake.external_intake_data.contains({"firstName": "Coulter"})).one()
    assert len(intake.intake_questions) == 0
    assert intake.cleaned_external_intake_data is None
    assert not intake.summary

    # The job should not run on an intake without the proper info.
    try:
        perform(intake.id)
    except ValueError as e:
        assert str(e) == "intake data not prepared"

    # Add questions and cleaned intake data.
    # This simulates running the `generate_intake_questions` job and collecting
    # answers from the patient.
    intake.cleaned_external_intake_data = ExternalIntakeData(
        reason_for_visit='Follow up acne',
        appointment_type='Dermatology │ Existing Patient',
        additional_info='Patient is a 19-year-old male with a height of 6\'0" and weight of 155 lbs.',
        appointment_category='Virtual Dermatology',
        )
    intake.save()

    # The job should not run on an intake without answered questions.
    try:
        perform(intake.id)
    except ValueError as e:
        assert str(e) == "intake questions not answered"

    intake.intake_questions = [
            IntakeSchemaQuestion(
                order=0,
                question='How has your acne changed since your last visit?',
                required_details='Description of any changes in the severity, frequency, or appearance of acne since the last visit.',
                answer='The acne has worsened with more breakouts on the cheeks and forehead. It seems to be more inflamed than before.',
                answer_timestamp=None
                ),
            IntakeSchemaQuestion(
                order=1,
                question='Have you noticed any specific triggers or factors that seem to worsen your acne?',
                required_details='Information about any environmental, dietary, or lifestyle factors that may be contributing to acne flare-ups.',
                answer='Yes, I think stress and eating more dairy products have been triggering the breakouts.',
                answer_timestamp=None
                ),
            IntakeSchemaQuestion(
                order=2,
                question='What treatments or skincare products have you been using for your acne since your last visit?',
                required_details='Details about any medications, topical treatments, or skincare routines currently being used.',
                answer='I have been using a topical retinoid cream (Tretinoin) every night and an over-the-counter benzoyl peroxide wash in the morning. I also started using a new moisturizer.',
                answer_timestamp=None
                ),
            IntakeSchemaQuestion(
                order=3,
                question='Have you experienced any side effects from the treatments you are using?',
                required_details='Information about any adverse reactions or side effects from current acne treatments.',
                answer='No',
                answer_timestamp=None
                ),
            IntakeSchemaQuestion(
                order=4,
                question='Do you have any other skin concerns or symptoms that have developed recently?',
                required_details='Details about any new or additional skin issues or symptoms that have arisen.',
                answer='I have noticed some dryness and peeling around my nose and mouth, but I think that might be from the retinoid cream.',
                answer_timestamp=None
                ),
            IntakeSchemaQuestion(
                order=5,
                question='Have you made any changes to your diet or lifestyle that might affect your skin?',
                required_details='Information about any recent changes in diet, exercise, or lifestyle that could impact skin health.',
                answer='I eat one raw sardine each morning with the rise of the sun',
                answer_timestamp=None
                ),
            IntakeSchemaQuestion(
                order=6,
                question='Are you currently taking any other medications or supplements?',
                required_details='List of any other medications or supplements being taken that could interact with acne treatments.',
                answer='Molly',
                answer_timestamp=None
                )
            ]
    intake.save()

    perform(intake.id)

    intake = Intake.get(id=intake.id)
    summary = intake.summary
    assert summary
    assert len(summary.split()) <= 300 # Roughly under 250 words.

    # The job should be idempotent.
    perform(intake.id)
    intake = Intake.get(id=intake.id)
    assert summary == intake.summary
