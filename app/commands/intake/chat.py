import time

from app.models.intake.intake import Intake, IntakeSchemaQuestion


def perform(intake: Intake, user_chat_input: str | None) -> IntakeSchemaQuestion | None:
    """
    Handles processing a user chat input for an intake process and letting the user know what to answer next

    1. If the user has answered the existing questions, reach out to an LLM and ask for any follow-up questions
    2. Persist the response from the user and return the next question to ask

    Remember, this is not an authenticated endpoint, so we cannot return any content with PII
    """
    unanswered_questions = [q for q in intake.intake_questions if not q.answer]

    if not unanswered_questions:
        # All questions have been answered, we would handle follow-up questions here
        # This would involve LLM interaction - not implementing this part for now
        return None

    # Sort unanswered questions by order
    unanswered_questions.sort(key=lambda q: q.order)

    # Process the user input if provided
    if user_chat_input:
        current_question = unanswered_questions[0]
        assert current_question

        current_question.answer = user_chat_input
        current_question.answer_timestamp = int(time.time())
        intake.save()

        unanswered_questions = [q for q in unanswered_questions if not q.answer]

    # Return the next question if there are any left
    if unanswered_questions:
        return unanswered_questions[0]

    return None
