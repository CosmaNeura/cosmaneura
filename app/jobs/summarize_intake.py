"""
Summarize all the intake data for a given appointment.
"""

import json
from datetime import datetime
from textwrap import dedent

from typeid import TypeID

from app import log
from app.celery import celery
from app.configuration.openai import openai, DEFAULT_MULTI_MODAL_MODEL

from activemodel.types import TypeIDType
from app.models.ai_visit_note import AIVisitNote
from app.models.appointment import Appointment
from app.models.doctor import Doctor
from app.models.intake.intake import Intake
from app.models.patient import Patient

def perform(intake_id: str | TypeID | TypeIDType):
    """
    Generate intake questions for a patient of a doctor.
    """
    log.info("generating intake summary", intake_id=intake_id)

    intake = Intake.one(id=intake_id)

    if intake.summary:
        log.info("intake summary already exists, skipping generation",
                 intake_id=intake_id)
        return

    if not intake.cleaned_external_intake_data:
        log.error("summarize_intake: intake data not prepared",
                  intake_id=intake_id)
        raise ValueError("intake data not prepared")

    missing_answers = all(
        question.answer is None for question in intake.intake_questions
    )

    if missing_answers:
        log.error("summarize_intake: intake questions not answered", intake_id=intake_id)
        raise ValueError("intake questions not answered")

    summary = generate_intake_summary(intake)
    assert summary

    intake.summary = summary
    intake.save()

    log.info("generated intake summary", intake_id=intake_id)

perform_celery = celery.task(perform)

def queue(*args, **kwargs):
    "always use this method to queue jobs so it's easy to swap out the job system"
    perform_celery.delay(*args, **kwargs)

def days_since(past_dt: datetime) -> int:
    past_date = past_dt.date()
    today = datetime.today().date()
    return (today - past_date).days

def generate_prompt(intake: Intake) -> str:
    doctor = Doctor.one(id=intake.doctor_id)
    appointment = Appointment.one(id=intake.appointment_id)
    patient = Patient.one(id=appointment.patient_id)

    # TODO This should be time-limited, e.g. don't pull a note from 5 years ago.
    last_note = (
        AIVisitNote.select()
        .join(Appointment, AIVisitNote.appointment_id == Appointment.id)
        .join(Patient, Appointment.patient_id == Patient.id)
        .where(
            (AIVisitNote.doctor_id == doctor.id)
            & (Patient.id == patient.id)
        ).order_by(Appointment.updated_at.desc())
        .limit(1)
        .first()
    )

    follow_up_questions = [
        {"question": q.question, "answer": q.answer}
        for q in sorted(intake.intake_questions, key=lambda q: q.order)
    ]

    last_visit_section = ""
    if last_note is not None:
        last_appt = Appointment.one(id=last_note.appointment_id)
        last_visit_section = dedent(f"""
        This particular patient has seen you before. Here is the clinical note from their last visit, which was {days_since(last_appt.appointment_date)} days ago:

        <CLINICAL_NOTE>
        {json.dumps(last_note.note, indent=2)}
        </CLINICAL_NOTE>
        """)

    # We want summaries to be concise, taking no more than a minute of the
    # doctor's time to read. An average reader reads about 4 words per second.
    max_words = 250

    return dedent(f"""
    You are a doctor with the following bio:

    <BIO>
    {doctor.biography}
    </BIO>

    A patient is coming in for an appointment with you.
    Your office has collected some information from the patient during intake to help you prepare.

    <INTAKE>
    {json.dumps(intake.cleaned_external_intake_data.dict(), indent=2)}
    </INTAKE>

    The patient was asked some follow up questions, here are the questions and answers:

    <FOLLOW_UP>
    {json.dumps(follow_up_questions, indent=2)}
    </FOLLOW_UP>

    {last_visit_section}

    # Here are your instructions:

    1. Summarize the INTAKE and FOLLOW_UP information.
      - Your goal is to produce a summary that will quickly prepare YOU for the patient's visit.
      - You will be reading the summary immediately before you begin the appointment.

    2. Keep in mind your BIO and the kind of visit that the patient is coming in for.
      - This will determine what information is most important to include in your summary.

    3. Be *specific*. Do not exclude any important details from the INTAKE or FOLLOW_UP.
      - e.g. If the patient is coming in for diabetes care and they mentioned that their average glucose level is 178, make sure that value is included in your summary.
      - e.g. If the patient is coming in for a dermatology visit and they mention having poison ivy, make sure that is included in your summary.

    4. Use markdown formatting (especially lists) to make your summary easy to read at a glance.
      - Do not include any sections or headings in your output.

    5. Your summary should be no more than {max_words} words.

    6. Do NOT include any intro or conclusion in your output.

    7. Do NOT include any questions in your output.

    8. Do NOT reproduce or restate any part of these instructions.
    """)

def generate_intake_summary(intake: Intake) -> str | None:
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
