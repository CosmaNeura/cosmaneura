from app.models.intake.intake import Intake


def perform(intake: Intake, user_chat_input: str):
    """
    Handles processing a user chat input for an intake process and letting the user know what to answer next

    1. If the user has answered the existing questions, reach out to an LLM and ask for any follow-up questions
    2. Persist the response from the user and return the next question to ask
    """
    pass
