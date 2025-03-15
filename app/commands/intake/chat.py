from app.models.intake.doctor_intake_schema import IntakeQuestion
from app.models.intake.intake import Intake


def perform(intake: Intake, user_chat_input: str | None) -> IntakeQuestion | None:
    """
    Handles processing a user chat input for an intake process and letting the user know what to answer next

    1. If the user has answered the existing questions, reach out to an LLM and ask for any follow-up questions
    2. Persist the response from the user and return the next question to ask

    Remember, this is not an authenticated endpoint, so we cannot return any content with PII
    """

    # Process the user input if provided
    if user_chat_input:
        # Here you would handle storing the response
        # ...

        # Determine next question based on intake status
        # For now, returning a sample next question
        pass

    # Return the next question to ask
    return IntakeQuestion(
        key="medical_history",
        question="Do you have any pre-existing medical conditions?",
        description="Please list any diagnosed conditions like diabetes, hypertension, etc.",
        order=2,
    )
