"""
Summarize all the intake data for a given appointment.
"""

import json
import os
from textwrap import dedent
from typing import cast

from typeid import TypeID

from app import log
from app.celery import celery_app
from app.configuration.openai import DEFAULT_MULTI_MODAL_MODEL, openai
from app.services.patient_context import days_since, latest_note_for_patient
from app.services.terminology import terminology_terms

from activemodel.types import TypeIDType
from app.models.appointment import Appointment
from app.models.doctor import Doctor
from app.models.intake.intake import Intake
from app.models.patient import Patient


def should_use_test_openai_fallback() -> bool:
    return os.environ.get("PYTHON_ENV") == "test" and not (
        os.environ.get("AZURE_OPENAI_API_KEY")
        and os.environ.get("AZURE_OPENAI_ENDPOINT")
    )


def _local_generate_intake_summary(intake: Intake) -> str:
    cleaned_intake_data = intake.cleaned_external_intake_data
    assert cleaned_intake_data is not None

    follow_up_lines = [
        f"- {question.question}: {question.answer}"
        for question in sorted(
            intake.intake_questions, key=lambda question: question.order
        )
        if question.answer
    ]

    return dedent(f"""
    - Reason for Visit: {cleaned_intake_data.reason_for_visit}
    - Appointment Type: {cleaned_intake_data.appointment_type}
    - Appointment Category: {cleaned_intake_data.appointment_category or "Not provided"}
    - Additional Info: {cleaned_intake_data.additional_info}

    {chr(10).join(follow_up_lines)}
    """).strip()


def perform(intake_id: str | TypeID | TypeIDType):
    """
    Generate intake questions for a patient of a doctor.
    """
    log.info("generating intake summary", intake_id=intake_id)

    intake = Intake.one(id=intake_id)

    if intake.summary:
        log.info(
            "intake summary already exists, skipping generation", intake_id=intake_id
        )
        return

    if not intake.cleaned_external_intake_data:
        log.error("summarize_intake: intake data not prepared", intake_id=intake_id)
        raise ValueError("intake data not prepared")

    missing_answers = all(
        question.answer is None for question in intake.intake_questions
    )

    if missing_answers:
        log.error(
            "summarize_intake: intake questions not answered", intake_id=intake_id
        )
        raise ValueError("intake questions not answered")

    summary = generate_intake_summary(intake)
    assert summary

    intake.summary = summary
    intake.save()

    log.info("generated intake summary", intake_id=intake_id)


perform_celery = celery_app.task(perform)


def queue(*args, **kwargs):
    "always use this method to queue jobs so it's easy to swap out the job system"
    perform_celery.delay(*args, **kwargs)


def generate_prompt(intake: Intake) -> str:
    doctor = Doctor.one(id=intake.doctor_id)
    t = terminology_terms(doctor)
    appointment = Appointment.one(id=intake.appointment_id)
    patient = Patient.one(id=appointment.patient_id)

    last_note = latest_note_for_patient(doctor.id, patient.id)

    follow_up_questions = [
        {"question": q.question, "answer": q.answer}
        for q in sorted(intake.intake_questions, key=lambda q: q.order)
    ]

    last_visit_section = ""
    if last_note is not None:
        last_appt = Appointment.one(id=last_note.appointment_id)
        last_visit_section = dedent(f"""
        This particular {t.patient} has seen you before. Here is the clinical note from their last visit, which was {days_since(last_appt.appointment_date)} days ago:

        <CLINICAL_NOTE>
        {json.dumps(last_note.note, indent=2)}
        </CLINICAL_NOTE>
        """)

    # We want summaries to be concise, taking no more than a minute of the
    # doctor's time to read. An average reader reads about 4 words per second.
    max_words = 250

    cleaned_intake_data = intake.cleaned_external_intake_data
    assert cleaned_intake_data is not None

    return dedent(f"""
    You are a {t.provider} with the following bio:

    <BIO>
    {doctor.biography}
    </BIO>

    A {t.patient} is coming in for an appointment with you.
    Your office has collected some information from the {t.patient} during intake to help you prepare.

    <INTAKE>
    {json.dumps(cast(dict, cleaned_intake_data.dict()), indent=2)}
    </INTAKE>

    The {t.patient} was asked some follow up questions, here are the questions and answers:

    <FOLLOW_UP>
    {json.dumps(follow_up_questions, indent=2)}
    </FOLLOW_UP>

    {last_visit_section}

    # Here are your instructions:

    1. Summarize the INTAKE and FOLLOW_UP information.
      - Your goal is to produce a summary that will quickly prepare YOU for the {t.patient}'s visit.
      - You will be reading the summary immediately before you begin the appointment.

    2. Keep in mind your BIO and the kind of visit that the {t.patient} is coming in for.
      - This will determine what information is most important to include in your summary.

    3. Be *specific*. Do not exclude any important details from the INTAKE or FOLLOW_UP.
      - e.g. If the {t.patient} is coming in for diabetes care and they mentioned that their average glucose level is 178, make sure that value is included in your summary.
      - e.g. If the {t.patient} is coming in for a dermatology visit and they mention having poison ivy, make sure that is included in your summary.

    4. Use markdown formatting (especially lists) to make your summary easy to read at a glance.
      - Do not include any sections or headings in your output.

    5. Your summary should be no more than {max_words} words.

    6. Do NOT include any intro or conclusion in your output.

    7. Do NOT include any questions in your output.

    8. Do NOT reproduce or restate any part of these instructions.
    """)


def generate_intake_summary(intake: Intake) -> str | None:
    if should_use_test_openai_fallback():
        return _local_generate_intake_summary(intake)

    prompt = generate_prompt(intake)

    response = openai.chat.completions.create(
        model=DEFAULT_MULTI_MODAL_MODEL,
        messages=[{"role": "system", "content": prompt}],
        temperature=0,
        top_p=0.1,
    )

    summary = response.choices[0].message.content

    if not summary or len(summary.strip()) < 50:
        log.error("openai failed to generate intake summary", intake_id=intake.id)
        return None

    return summary.strip()
