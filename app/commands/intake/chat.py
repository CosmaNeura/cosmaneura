import time

from app import log
from app.jobs.intake.summarize import queue as queue_intake_summarization
from app.services.external_webhook_inbox import provider_log_ref

from app.models.intake.intake import Intake, IntakeSchemaQuestion


def perform(intake: Intake, user_chat_input: str | None) -> IntakeSchemaQuestion | None:
    """
    Handles processing a user chat input for an intake process and letting the user know what to answer next

    1. If the user has answered the existing questions, reach out to an LLM and ask for any follow-up questions
    2. Persist the response from the user and return the next question to ask

    Remember, this is not an authenticated endpoint, so we cannot return any content with PII
    """

    # Validate just in case. We never expect this to fail.
    validate_question_structure(intake)

    current_question = intake.next_unanswered_question()
    if not current_question:
        # This should never happen.
        log.error(
            "no current question",
            intake_ref=provider_log_ref(str(intake.id)),
        )
        return None

    # `None` means "just tell me where I am" (the GET peek). An empty string is
    # a deliberate skip and has to be stored, or the interview loops forever on
    # a question the patient chose not to answer. `is_answered` keys off the
    # timestamp precisely so an empty answer still advances.
    if user_chat_input is None:
        return current_question

    # Find the matching question in intake_questions and update it.
    for index, question in enumerate(intake.intake_questions):
        if question.question == current_question.question:
            intake.intake_questions[index].answer = user_chat_input
            intake.intake_questions[index].answer_timestamp = int(time.time())

            # IMPORTANT: JSON objects are not automatically marked as dirty.
            intake.flag_modified("intake_questions")

            log.info("updating question", order=question.order)
            intake.save()
            break

    if next_question := intake.next_unanswered_question():
        return next_question
    else:
        log.info(
            "queueing intake summarization",
            intake_ref=provider_log_ref(str(intake.id)),
        )
        queue_intake_summarization(intake.id)
        return None


def validate_question_structure(intake: Intake) -> None:
    """
    1. Make sure all questions are unique
    2. Make sure order is unique
    3. Make sure physician-authored keys are unique
    4. Make sure every condition points at a key that exists

    (3) and (4) are no-ops for model-generated intakes, which carry neither
    keys nor conditions. They matter for physician-authored sets, where a
    condition anchored to a missing key would silently hide its question
    forever -- the patient would never see it and the prep would still complete.
    """
    # Get all questions from the intake
    questions = intake.intake_questions

    if not questions:
        raise ValueError("No questions found in the intake.")

    # Check for uniqueness of questions and order
    question_texts = []
    orders = []
    keys = []

    for question in questions:
        if question.question in question_texts:
            raise ValueError(f"Duplicate question found: '{question.question}'")
        question_texts.append(question.question)

        if question.order in orders:
            raise ValueError(f"Duplicate order found: {question.order}")
        orders.append(question.order)

        if question.key is not None:
            if question.key in keys:
                raise ValueError(f"Duplicate question key found: '{question.key}'")
            keys.append(question.key)

    order_by_key = {q.key: q.order for q in questions if q.key}

    for question in questions:
        if not question.show_if:
            continue

        gate_order = order_by_key.get(question.show_if.key)

        if gate_order is None:
            raise ValueError(
                f"Question '{question.key or question.order}' is conditional on "
                f"unknown key '{question.show_if.key}'"
            )

        # A question may only depend on one asked before it. That is the real
        # semantic -- you cannot branch on an answer you have not collected --
        # and enforcing it as an ordering rule also rules out self-reference and
        # dependency cycles in one check. Both of those are worse than they
        # look: every question in a cycle is permanently invisible, so the prep
        # reports itself complete having asked nothing, and a brief gets written
        # from no answers at all.
        if gate_order >= question.order:
            raise ValueError(
                f"Question '{question.key or question.order}' is conditional on "
                f"'{question.show_if.key}', which is not asked before it"
            )
