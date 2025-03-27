"""
Generate intake questions for a patient of a doctor.
"""

import json
from typeid import TypeID
from textwrap import dedent
from datetime import datetime

from app import log
from app.celery import celery
from app.configuration.openai import openai

from activemodel.types import TypeIDType
from app.models.intake.intake import Intake, IntakeSchemaQuestion
from app.models.ai_visit_note import AIVisitNote
from app.models.doctor import Doctor
from app.models.patient import Patient
from app.models.appointment import Appointment

OPENAI_MODEL = "gpt-4o"

def perform(intake_id: str | TypeID | TypeIDType):
    """
    Generate intake questions for a patient of a doctor.
    """
    log.info("generating intake questions", intake_id=intake_id)

    intake = Intake.one(id=intake_id)

    if len(intake.intake_questions) > 0:
        log.info("intake questions already exist, skipping generation",
                 intake_id=intake_id)
        return

    questions = generate_intake_questions(intake)
    assert questions

    intake.intake_questions = questions
    intake.save()

    log.info(f"generated {len(questions)} intake questions", intake_id=intake_id)

perform_celery = celery.task(perform)

def queue(*args, **kwargs):
    "always use this method to queue jobs so it's easy to swap out the job system"
    perform_celery.delay(*args, **kwargs)

def days_since(past_dt: datetime) -> int:
    past_date = past_dt.date()
    today = datetime.today().date()
    return (today - past_date).days

def extraction_prompt(intake: Intake, doctor: Doctor) -> str:
    return dedent("""
        You are a doctor that is about to see a patient. Here is your bio:

        <BIO>
        {doctor.biography}
        </BIO>

        I'm going to give you the INTAKE information for the patient. It is structured as JSON.

        <INTAKE>
        {json.dumps(intake.external_intake_data, indent=2)}
        </INTAKE>

        # Here are your instructions

        1. Extract the appointment type and (if available) its category from the INTAKE.

        2. Extract the patient's main reason for the visit from the INTAKE.
        - If not present, say "Not provided"

        3. Extract any additional information from INTAKE that is relevant to your preparation for this appointment.
        - Keep in mind the type of doctor you are (see your BIO), the appointment type, and the patient's main reason for the visit.
        - Examples of relevant information include: patient vitals, measurements, past medical history, family history, etc.
        - Do NOT extract administrative information like name, DOB, insurance, doctor name, appointment date, addresses, or payment info.
        - Do NOT comment on information that is missing or not present, only on what information is there.

        4. Respond only in JSON with the following SCHEMA.

        <SCHEMA>
        {{
          appointment_type: string
          appointment_category: string | null
          reason: string // The patient's main reason for the visit
          additional_info: string // Additional info relevant to the doctor's preparation
        }}
        </SCHEMA>
    """)

def extract_intake_data(intake: Intake, doctor: Doctor) -> dict[str, str]:
    prompt = extraction_prompt(intake, doctor)
    response = openai.chat.completions.create(
        model=OPENAI_MODEL,
        messages=[{"role": "system", "content": prompt}],
        temperature=0,
        top_p=0.1,
    )
    raw_intake_data = response.choices[0].message.content
    assert raw_intake_data
    intake_data = json.loads(raw_intake_data)

    print(intake_data)

    return intake_data

def generate_questions_prompt(intake: Intake) -> str:
    doctor = Doctor.one(id=intake.doctor_id)
    appointment = Appointment.one(id=intake.appointment_id)
    patient = Patient.one(id=appointment.patient_id)

   intake_data = extract_intake_data(intake, doctor)

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

    last_visit_section = ""
    if last_note is not None:
        last_appt = Appointment.one(id=last_note.appointment_id)
        last_visit_section = dedent(f"""
        The patient has seen you before. Here is the clinical note from their last visit, which was {days_since(last_appt.appointment_date)} days ago:

        <CLINICAL_NOTE>
        {json.dumps(last_note.note, indent=2)}
        </CLINICAL_NOTE>
        """)

    # We collect a bunch of information that we don't use in order to force
    # the model to think step by step in the context of a JSON-formatted response.
    # JSON mode limits a model's reasoning capabilities otherwise.
    # See https://arxiv.org/html/2408.02442v3.

    return dedent("""
    You are a doctor with the following bio:

    <BIO>
    {doctor.biography}
    </BIO>

    A patient is coming in for an appointment with you.
    The patient has filled out a preliminary intake form with the following information:

    <INTAKE>
    {json.dumps(intake_data, indent=2)}
    </INTAKE>

    {last_visit_section}

    Here are your instructions:
    1. Examine the INTAKE information and determine:
      - What is the patient's chief complaint?
      - What type of appointment is the patient coming in for?
    2. If present, review the most recent CLINICAL_NOTE for this patient.
      - Does the patient's chief complaint relate to their previous visit?
      - If no CLINICAL_NOTE is present, ignore this step.
    3. Review your bio and think about why this patient, with this chief complaint, would come to see you.
    4. Think about what additional information you need to help the patient with their chief complaint.
    5. Generate questions to get the information you need from the patient.
      - You may generate no more than 10 questions.
      - Questions will be collected as free-form text, but you may ask multiple-choice questions.
      - Order often matters. Specify the order that the questions should be asked in.
      - Do NOT ask questions that are already answered in the INTAKE form.
      - Do NOT ask administrative questions like "What is your name?" or "What is your date of birth?"
      - Questions may NOT include PII like the patient's name, address, DOB, phone number, email address, or any PII about you, the doctor.
    6. Respond only in JSON format with the following SCHEMA:

    interface Question {{
      // The order that the question should be asked in. Lower numbers are asked
      // first. The 1st question will have order=0, the 2nd order=1, and so on.
      order: int
      // The question to ask the patient.
      question: str
      // What information would an answer to this question have to include in
      // order for it to constitute a full answer to the question?
      required_details: str
    }}

    <SCHEMA>
    {{
      // The patient's cheif complaint, as extracted from the INTAKE form.
      chief_complaint: str

      // The type of appointment the patient is coming in for, as extracted from
      // the INTAKE form.
      appointment_type: str

      // How the patient's chief complaint relates to their previous visit
      // CLINICAL_NOTE (if they had one).
      relation_to_previous_visit: str | null

      // The questions to ask the patient.
      questions: Question[]
    }}
    </SCHEMA>
    """)

def generate_intake_questions(intake: Intake) -> list[IntakeSchemaQuestion]:
    prompt = generate_questions_prompt(intake)
    response = openai.chat.completions.create(
        model=OPENAI_MODEL,
        messages=[{"role": "system", "content": prompt}],
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

    print(intake_questions)

    return intake_questions
