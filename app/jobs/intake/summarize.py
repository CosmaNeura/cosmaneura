"""
Summarize all the intake data for a given appointment.
"""

import json
import os
from datetime import datetime
from textwrap import dedent
from typing import Any, cast

from typeid import TypeID

from app import log
from app.celery import celery_app
from app.configuration.openai import DEFAULT_MULTI_MODAL_MODEL, openai
from app.services import patient_history

from activemodel.types import TypeIDType
from app.models.ai_visit_note import AIVisitNote
from app.models.appointment import Appointment
from app.models.doctor import Doctor
from app.models.intake.intake import Intake
from app.models.patient import Patient

MAX_NOTE_PROMPT_CHARS = 4_000
"cap each prior note's contribution to the context prompt to bound token usage"


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
    Generate the intake summary and the provider-facing patient context for a
    completed intake.
    """
    log.info("generating intake summary", intake_id=intake_id)

    intake = Intake.one(id=intake_id)

    if intake.summary and intake.patient_context:
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

    if not intake.summary:
        summary = generate_intake_summary(intake)
        assert summary

        intake.summary = summary
        intake.save()

        log.info("generated intake summary", intake_id=intake_id)

    # None is a valid outcome (patients with no prior history), so this runs on
    # every invocation for such patients instead of being latched like summary
    if not intake.patient_context:
        context = generate_patient_context(intake)

        if context:
            intake.patient_context = context
            intake.save()

            log.info("generated patient context", intake_id=intake_id)


perform_celery = celery_app.task(perform)


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
        .where((AIVisitNote.doctor_id == doctor.id) & (Patient.id == patient.id))
        .order_by(cast(Any, Appointment.updated_at).desc())
        .limit(1)
        .first()
    )
    last_note = cast(AIVisitNote | None, last_note)

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

    cleaned_intake_data = intake.cleaned_external_intake_data
    assert cleaned_intake_data is not None

    return dedent(f"""
    You are a doctor with the following bio:

    <BIO>
    {doctor.biography}
    </BIO>

    A patient is coming in for an appointment with you.
    Your office has collected some information from the patient during intake to help you prepare.

    <INTAKE>
    {json.dumps(cast(dict, cleaned_intake_data.dict()), indent=2)}
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


def _local_generate_patient_context(history: patient_history.PatientHistory) -> str:
    "deterministic offline stand-in for the LLM, mirroring its attribution format"
    lines = []

    for prior_note in history.prior_notes:
        note_digest = "; ".join(
            f"{section}: {content}"
            for section, content in list(prior_note.note.items())[:3]
        )
        lines.append(f"- {note_digest} (visit note, {prior_note.days_ago} days ago)")

    for entry in history.prior_intake_answers:
        lines.append(
            f"- {entry.question} {entry.answer} "
            f"(patient-reported intake answer, {entry.days_ago} days ago) — unconfirmed"
        )

    return "\n".join(lines)


def patient_context_prompt(
    intake: Intake, history: patient_history.PatientHistory
) -> str:
    doctor = Doctor.one(id=intake.doctor_id)

    note_sections = [
        dedent(f"""
        <VISIT_NOTE days_ago="{prior_note.days_ago}">
        {json.dumps(prior_note.note, indent=2)[:MAX_NOTE_PROMPT_CHARS]}
        </VISIT_NOTE>""")
        for prior_note in history.prior_notes
    ]

    answer_sections = [
        dedent(f"""
        <PATIENT_REPORTED days_ago="{entry.days_ago}">
        Q: {entry.question}
        A: {entry.answer}
        </PATIENT_REPORTED>""")
        for entry in history.prior_intake_answers
    ]

    cleaned_intake_data = intake.cleaned_external_intake_data
    current_reason = (
        cleaned_intake_data.reason_for_visit if cleaned_intake_data else "Not provided"
    )

    return dedent(f"""
    You are a doctor with the following bio:

    <BIO>
    {doctor.biography}
    </BIO>

    A patient is coming in for an appointment with you. Their current reason for the visit: {current_reason}

    Below is this patient's history from YOUR OWN practice records. VISIT_NOTE entries are clinical notes from the patient's prior visits with you. PATIENT_REPORTED entries are answers the patient typed into previous intake forms — they are self-reported and have NOT been clinically verified.

    <HISTORY>
    {chr(10).join(note_sections)}
    {chr(10).join(answer_sections)}
    </HISTORY>

    # Here are your instructions:

    1. Assemble a short "patient context" profile to help you prepare for the visit: ongoing conditions, medications, allergies, prior treatments and their outcomes, patterns across visits, and anything relevant to the current reason for the visit.

    2. Attribute EVERY item to its source and age, e.g. "(visit note, 42 days ago)" or "(patient-reported intake answer, 90 days ago)".

    3. Items supported only by PATIENT_REPORTED entries are unconfirmed. Append "— unconfirmed" to each of those items.

    4. Do NOT invent, infer, or extrapolate facts that are not explicitly in the HISTORY.

    5. Do NOT include administrative details (addresses, insurance, phone numbers, emails).

    6. Use markdown bullets. No headings, no intro, no conclusion.

    7. No more than 200 words.
    """)


def generate_patient_context(intake: Intake) -> str | None:
    """
    Provider-facing "expanded profile", synthesized exclusively from records the
    practice already holds (prior visit notes and previous intake answers) —
    never from external sources. Every item is source-attributed and
    self-reported items are marked unconfirmed, so the provider can judge what
    needs validation. Returns None when the patient has no prior history.
    """
    history = patient_history.collect(intake)

    if history.is_empty():
        return None

    if should_use_test_openai_fallback():
        return _local_generate_patient_context(history)

    prompt = patient_context_prompt(intake, history)

    response = openai.chat.completions.create(
        model=DEFAULT_MULTI_MODAL_MODEL,
        messages=[{"role": "system", "content": prompt}],
        temperature=0,
        top_p=0.1,
    )

    context = response.choices[0].message.content

    if not context or len(context.strip()) < 20:
        log.error("openai failed to generate patient context", intake_id=intake.id)
        return None

    return context.strip()
