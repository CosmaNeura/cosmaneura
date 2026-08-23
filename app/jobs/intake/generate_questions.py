"""
Generate intake questions for a patient of a doctor.
"""

import json
from textwrap import dedent
from typing import Any, cast

from typeid import TypeID

from app import log
from app.celery import celery_app
from app.configuration.openai import DEFAULT_MULTI_MODAL_MODEL, openai
from app.environments import should_use_test_openai_fallback
from app.services.patient_context import days_since, latest_note_for_patient
from app.services.terminology import terminology_terms

from activemodel.types import TypeIDType
from app.models.appointment import Appointment
from app.models.doctor import Doctor
from app.models.intake.intake import ExternalIntakeData, Intake, IntakeSchemaQuestion
from app.models.patient import Patient


def _find_intake_value(intake_data: dict[str, Any], field_name: str) -> str | None:
    forms = intake_data.get("forms") or []
    for form in forms:
        values = form.get("values") or []
        for value in values:
            if value.get("name") != field_name:
                continue

            field_value = value.get("value")
            if field_value:
                return str(field_value)

    return None


def _local_extract_intake_data(intake: Intake) -> ExternalIntakeData:
    intake_data = cast(dict[str, Any], intake.external_intake_data or {})
    reason = _find_intake_value(
        intake_data, "What is the primary reason for making this appointment?"
    )
    appointment_type = str(intake_data.get("type") or "General consultation")
    appointment_category = intake_data.get("category")

    details = [
        _find_intake_value(intake_data, "Patient Sex"),
        _find_intake_value(intake_data, "Patient Height"),
        _find_intake_value(intake_data, "Patient Weight"),
    ]
    additional_info = (
        ", ".join(detail for detail in details if detail) or "Not provided"
    )

    return ExternalIntakeData(
        reason_for_visit=reason or "Not provided",
        appointment_type=appointment_type,
        additional_info=additional_info,
        appointment_category=str(appointment_category)
        if appointment_category
        else None,
    )


def _local_generate_intake_questions(intake: Intake) -> list[IntakeSchemaQuestion]:
    intake_data = intake.cleaned_external_intake_data
    assert intake_data is not None

    reason = intake_data.reason_for_visit or "your main concern"

    return [
        IntakeSchemaQuestion(
            order=1,
            question=f"How has {reason.lower()} changed over time?",
            required_details="Include onset, duration, and any recent changes.",
        ),
        IntakeSchemaQuestion(
            order=2,
            question="What treatments, medications, or home remedies have you tried?",
            required_details="Include what you tried and whether it helped.",
        ),
        IntakeSchemaQuestion(
            order=3,
            question="Are there any triggers, related symptoms, or relevant medical history?",
            required_details="Include triggers, associated symptoms, and pertinent history.",
        ),
    ]


def perform(intake_id: str | TypeID | TypeIDType):
    """
    Generate intake questions for a patient of a doctor.
    """
    log.local(intake_id=intake_id)
    log.info("generating intake questions")

    intake = Intake.one(id=intake_id)
    doctor = Doctor.one(id=intake.doctor_id)

    intake_data = intake.cleaned_external_intake_data
    if intake_data is None:
        log.info("cleaning intake information", intake_id=intake_id)
        intake_data = extract_intake_data(intake, doctor)
        intake.cleaned_external_intake_data = intake_data
        intake.save()

    if intake.intake_questions:
        log.info(
            "intake questions already exist, skipping generation", intake_id=intake_id
        )
        return

    questions = generate_intake_questions(intake, doctor)
    assert questions

    intake.intake_questions = questions
    intake.save()

    log.info(f"generated {len(questions)} intake questions", intake_id=intake_id)


perform_celery = celery_app.task(perform)


def queue(*args, **kwargs):
    "always use this method to queue jobs so it's easy to swap out the job system"
    perform_celery.delay(*args, **kwargs)


def extraction_prompt(intake: Intake, doctor: Doctor) -> str:
    t = terminology_terms(doctor)
    return dedent(f"""
        You are a {t.provider} that is about to see a {t.patient}. Here is your bio:

        <BIO>
        {doctor.biography}
        </BIO>

        I'm going to give you the INTAKE information for the {t.patient}. It is structured as JSON.

        <INTAKE>
        {json.dumps(intake.external_intake_data, indent=2)}
        </INTAKE>

        # Here are your instructions

        1. Extract the appointment type and (if available) its category from the INTAKE.

        2. Extract the {t.patient}'s main reason for the visit from the INTAKE.
        - If not present, say "Not provided"

        3. Extract any additional information from INTAKE that is relevant to your preparation for this appointment.
        - Keep in mind the type of {t.provider} you are (see your BIO), the appointment type, and the {t.patient}'s main reason for the visit.
        - Examples of relevant information include: {t.patient} vitals, measurements, past medical history, family history, etc.
        - Do NOT extract administrative information like name, DOB, insurance, doctor name, appointment date, addresses, or payment info.
        - Do NOT comment on information that is missing or not present, only on what information is there.
        - Do NOT include any information already extracted in other steps.

        4. Respond only in JSON with the following SCHEMA.

        <SCHEMA>
        {{
          appointment_type: string
          appointment_category: string | null
          reason: string // The {t.patient}'s main reason for the visit
          additional_info: string // Additional info relevant to the {t.provider}'s preparation
        }}
        </SCHEMA>
    """)


def extract_intake_data(intake: Intake, doctor: Doctor) -> ExternalIntakeData:
    if should_use_test_openai_fallback():
        return _local_extract_intake_data(intake)

    prompt = extraction_prompt(intake, doctor)
    response = openai.chat.completions.create(
        model=DEFAULT_MULTI_MODAL_MODEL,
        messages=[{"role": "system", "content": prompt}],
        response_format={"type": "json_object"},
        temperature=0,
        top_p=0.1,
    )
    raw_intake_data = response.choices[0].message.content
    assert raw_intake_data
    intake_data = json.loads(raw_intake_data)

    return ExternalIntakeData(
        reason_for_visit=intake_data["reason"],
        appointment_type=intake_data["appointment_type"],
        additional_info=intake_data["additional_info"],
        appointment_category=intake_data.get("appointment_category"),
    )


def generate_questions_prompt(intake: Intake, doctor: Doctor) -> str:
    t = terminology_terms(doctor)
    appointment = Appointment.one(id=intake.appointment_id)
    patient = Patient.one(id=appointment.patient_id)

    intake_data = (
        intake.cleaned_external_intake_data.dict()
        if intake.cleaned_external_intake_data
        else {}
    )

    last_note = latest_note_for_patient(doctor.id, patient.id)

    last_visit_section = ""
    if last_note is not None:
        last_appt = Appointment.one(id=last_note.appointment_id)
        last_visit_section = dedent(f"""
        The {t.patient} has seen you before. Here is the clinical note from their last visit, which was {days_since(last_appt.appointment_date)} days ago:

        <CLINICAL_NOTE>
        {json.dumps(last_note.note, indent=2)}
        </CLINICAL_NOTE>
        """)

    # We collect a bunch of information that we don't use in order to force
    # the model to think step by step in the context of a JSON-formatted response.
    # JSON mode limits a model's reasoning capabilities otherwise.
    # See https://arxiv.org/html/2408.02442v3.

    return dedent(f"""
    You are a {t.provider} with the following bio:

    <BIO>
    {doctor.biography}
    </BIO>

    A {t.patient} is coming in for an appointment with you.
    The {t.patient} has filled out a preliminary intake form with the following information:

    <INTAKE>
    {json.dumps(intake_data, indent=2)}
    </INTAKE>

    {last_visit_section}

    Here are your instructions:
    1. Examine the INTAKE information and determine:
      - What is the {t.patient}'s chief complaint?
      - What type of appointment is the {t.patient} coming in for?
    2. If present, review the most recent CLINICAL_NOTE for this {t.patient}.
      - Does the {t.patient}'s chief complaint relate to their previous visit?
      - If no CLINICAL_NOTE is present, ignore this step.
    3. Review your bio and think about why this {t.patient}, with this chief complaint, would come to see you.
    4. Think about what additional information you need to help the {t.patient} with their chief complaint.
    5. Generate questions to get the information you need from the {t.patient}.
      - Generate at least 3 questions, not more than 10, and not 5.
      - Ask as few questions as possible to get the information you need.
      - Questions will be collected as free-form text, but you may ask multiple-choice questions.
      - Order often matters. Specify the order that the questions should be asked in.
      - Do NOT ask questions that are already answered in the INTAKE form.
      - Do NOT ask questions that request the same information in different ways, e.g. "What is your pain level?" and "How much does it hurt?"
      - Do NOT ask administrative questions like "What is your name?" or "What is your date of birth?"
      - Questions may NOT include PII like the {t.patient}'s name, address, DOB, phone number, email address, or any PII about you, the {t.provider}.
    6. Respond only in JSON format with the following SCHEMA:

    interface Question {{
      // The order that the question should be asked in. Lower numbers are asked
      // first. The 1st question will have order=0, the 2nd order=1, and so on.
      order: int
      // The question to ask the {t.patient}.
      question: str
      // What information would an answer to this question have to include in
      // order for it to constitute a full answer to the question?
      required_details: str
    }}

    <SCHEMA>
    {{
      // The {t.patient}'s chief complaint, as extracted from the INTAKE form.
      chief_complaint: str

      // The type of appointment the {t.patient} is coming in for, as extracted from
      // the INTAKE form.
      appointment_type: str

      // How the {t.patient}'s chief complaint relates to their previous visit
      // CLINICAL_NOTE (if they had one).
      relation_to_previous_visit: str | null

      // The questions to ask the {t.patient}.
      questions: Question[]
    }}
    </SCHEMA>
    """)


def generate_intake_questions(
    intake: Intake, doctor: Doctor
) -> list[IntakeSchemaQuestion]:
    if should_use_test_openai_fallback():
        return _local_generate_intake_questions(intake)

    prompt = generate_questions_prompt(intake, doctor)
    response = openai.chat.completions.create(
        model=DEFAULT_MULTI_MODAL_MODEL,
        messages=[cast(Any, {"role": "system", "content": prompt})],
        response_format={"type": "json_object"},
        temperature=0,
        top_p=0.1,
    )
    raw_questions = response.choices[0].message.content
    assert raw_questions
    parsed_questions = json.loads(raw_questions)

    # Strip out the additional information in the raw response that was included
    # just to help the model think step by step.
    intake_questions = [
        IntakeSchemaQuestion(
            order=int(q["order"]),
            question=q["question"],
            required_details=q["required_details"],
        )
        for q in parsed_questions["questions"]
    ]

    return intake_questions
