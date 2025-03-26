import time

from app import log

from app.models.intake.intake import Intake, IntakeSchemaQuestion


def perform(intake: Intake, user_chat_input: str | None) -> IntakeSchemaQuestion | None:
    """
    Handles processing a user chat input for an intake process and letting the user know what to answer next

    1. If the user has answered the existing questions, reach out to an LLM and ask for any follow-up questions
    2. Persist the response from the user and return the next question to ask

    Remember, this is not an authenticated endpoint, so we cannot return any content with PII
    """

    # just in case, we never expect this to fail
    validate_question_structure(intake)

    unanswered_questions = intake.unanswered_questions()

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

        # Find the matching question in intake_questions and update it
        for index, question in enumerate(intake.intake_questions):
            if question.question == current_question.question:
                intake.intake_questions[index].answer = user_chat_input
                intake.intake_questions[index].answer_timestamp = int(time.time())

                # IMPORTANT json objects are not automatically marked as dirty
                intake.flag_modified("intake_questions")

                log.info("updating question", order=question.order)
                intake.save()
                break

    # Return the next question if there are any left
    if unanswered_questions := intake.unanswered_questions():
        return unanswered_questions[0]

    return None


def validate_question_structure(intake: Intake) -> None:
    """
    1. Make sure all questions are unique
    2. Make sure order is unique
    """
    # Get all questions from the intake
    questions = intake.intake_questions

    if not questions:
        raise ValueError("No questions found in the intake.")

    # Check for uniqueness of questions and order
    question_texts = []
    orders = []

    for question in questions:
        if question.question in question_texts:
            raise ValueError(f"Duplicate question found: '{question.question}'")
        question_texts.append(question.question)

        if question.order in orders:
            raise ValueError(f"Duplicate order found: {question.order}")
        orders.append(question.order)
