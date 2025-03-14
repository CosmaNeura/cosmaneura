# seed fresh development databases with data

import json
from datetime import datetime

import factory
from factory.alchemy import SQLAlchemyModelFactory

from activemodel import SessionManager
from app.models.ai_visit_note import AIVisitNote
from app.models.ai_visit_transcript import AIVisitTranscript, TranscriptEntry
from app.models.appointment import Appointment
from app.models.doctor import Doctor
from app.models.doctor_note_example import DoctorNoteExample
from app.models.doctor_note_file import DoctorNoteFile
from app.models.doctor_note_schema import DoctorNoteSchema
from app.models.patient import Patient
from app.models.visit_audio_file import VisitAudioFile

from tests.factories import AppointmentFactory
from tests.seeds import get_clerk_seed_user

_, _, clerk_user = get_clerk_seed_user()

doctor = Doctor.find_or_create_by(clerk_id=clerk_user.id)

# random uuid not tied to a valid clerk id
doctor_2 = Doctor.find_or_create_by(clerk_id="user_01h45z113fexh8c1at7axm1r75")

patient = Patient.find_or_create_by(
    doctor_id=doctor.id,
    name="Ghengis Khan",
    date_of_birth=datetime.strptime("1970-10-31", "%Y-%m-%d"),
)

appointment = Appointment.find_or_create_by(
    doctor_id=doctor.id,
    patient_id=patient.id,
    appointment_date=datetime.strptime("2025-10-31", "%Y-%m-%d"),
    appointment_name="Battle Wound",
    appointment_notes_preview="Patient reports a wound from battle. Destroyed massive armies and is struggling to regain strength.\nRecommend lots of wine, rest, and some light exercise.",
)

# TODO should be moved to AM, still fiddling around with what the best pattern is here
for factory in SQLAlchemyModelFactory.__subclasses__():
    factory._meta.sqlalchemy_session_factory = SessionManager.get_instance().get_session
    factory._meta.sqlalchemy_session_persistence = "commit"

# to test multiple items in the ui (past browser height) and an empty note state
for i in range(20):
    result = AppointmentFactory(
        doctor_id=doctor.id,
        patient_id=patient.id,
        # appointment_date=datetime.strptime("2025-12-31", "%Y-%m-%d"),
        # appointment_name=f"Test Appointment {i + 1}: Wow AI really went crazy on this title, it's way too long. Will it ever end?",
    )

VisitAudioFile.find_or_create_by(
    doctor_id=doctor.id,
    patient_id=patient.id,
    appointment_id=appointment.id,
    azure_file_name="teresa-jensen-mock-appointment.mp4",
)

DoctorNoteFile.find_or_create_by(
    doctor_id=doctor.id,
    azure_file_name="teresa-jensen-mock-appointment-note-11-15-2024.pdf",
    note_type="Telehealth H&P Note",
)

DoctorNoteFile.find_or_create_by(
    doctor_id=doctor.id,
    azure_file_name="teresa-jensen-mock-appointment-note-11-22-2024.pdf",
    note_type="Telehealth SOAP Note",
)

ai_visit_transcript = AIVisitTranscript.find_or_create_by(
    doctor_id=doctor.id,
    patient_id=patient.id,
    appointment_id=appointment.id,
    duration=180,
    transcript_data=[
        TranscriptEntry(
            speaker="Doctor",
            content="Hello, how are you feeling today?",
            startTime=0,
            endTime=2000,
        ),
        TranscriptEntry(
            speaker="Patient",
            content="I've been having some headaches lately.",
            startTime=2500,
            endTime=5000,
        ),
        TranscriptEntry(
            speaker="Doctor",
            content="I see. How long have you been experiencing these headaches?",
            startTime=5500,
            endTime=8000,
        ),
        TranscriptEntry(
            speaker="Doctor",
            content="a" * 10000,  # generates a content string with 10k characters
            startTime=0,
            endTime=180000,
        ),
    ],
)

EXAMPLE_NOTE_SCHEMA_FIELDS = {
    "Chief Complaint": "Document the patient's primary reason for visit",
    "History of Present Illness": "Describe the chronological history of the current medical problem",
    "Assessment": "List the diagnoses and clinical impressions",
    "Plan": "Detail the treatment plan and next steps",
}


# This where condition below, instead of using find_or_initialized by, was a particularly hard won.
# Unfortunately, SQLAlchemy does not properly cast the JSON field to text when doing a comparison with a dictionary or even a string.
# So we need to manually force the comparison to an "as text" as you can see below.
# https://grok.com/share/bGVnYWN5_ba442958-cc1c-4a68-915b-099eb0b5ce1e
doctor_note_schema = DoctorNoteSchema.where(
    DoctorNoteSchema.doctor_id == doctor.id,
    DoctorNoteSchema.note_type == "Telehealth SOAP Note",
    DoctorNoteSchema.fields["fields"].astext == json.dumps(EXAMPLE_NOTE_SCHEMA_FIELDS),
).first()

if not doctor_note_schema:
    doctor_note_schema = DoctorNoteSchema(
        doctor_id=doctor.id,
        note_type="Telehealth SOAP Note",
        fields=EXAMPLE_NOTE_SCHEMA_FIELDS,
    ).save()

# same exact schema, different doctor, to better test the ui
doctor2_note_schema = DoctorNoteSchema.where(
    DoctorNoteSchema.doctor_id == doctor_2.id,
    DoctorNoteSchema.note_type == "In-person SOAP Note",
    DoctorNoteSchema.fields["fields"].astext == json.dumps(EXAMPLE_NOTE_SCHEMA_FIELDS),
).first()

if not doctor2_note_schema:
    doctor2_note_schema = DoctorNoteSchema(
        doctor_id=doctor_2.id,
        note_type="In-person SOAP Note",
        fields=EXAMPLE_NOTE_SCHEMA_FIELDS,
    ).save()

assert isinstance(doctor_note_schema, DoctorNoteSchema)

# TODO this is AI generated, we should improve seed data here
# each schema must have at least a single example
doctor_note_example = DoctorNoteExample.find_or_create_by(
    doctor_id=doctor.id,
    doctor_note_schema_id=doctor_note_schema.id,
    field_examples={
        "Chief Complaint": "Patient reports recurring headaches",
        "History of Present Illness": "Patient has been experiencing headaches recently. Frequency and duration to be determined.",
        "Assessment": "Recurring headaches of unknown etiology",
        "Plan": "Further evaluation needed to determine cause and appropriate treatment plan",
    },
)

doctor_note_example = DoctorNoteExample.find_or_create_by(
    doctor_id=doctor_2.id,
    doctor_note_schema_id=doctor2_note_schema.id,
    field_examples={
        "Chief Complaint": "Patient reports recurring headaches",
        "History of Present Illness": "Patient has been experiencing headaches recently. Frequency and duration to be determined.",
        "Assessment": "Recurring headaches of unknown etiology",
        "Plan": "Further evaluation needed to determine cause and appropriate treatment plan",
    },
)

AIVisitNote.find_or_create_by(
    doctor_id=doctor.id,
    appointment_id=appointment.id,
    ai_transcript_id=ai_visit_transcript.id,
    doctor_note_schema_id=doctor_note_schema.id,
    note={
        "Chief Complaint": "Patient reports recurring headaches",
        "History of Present Illness": "Patient has been experiencing headaches recently. Frequency and duration to be determined.",
        "Assessment": "Recurring headaches of unknown etiology",
        "Plan": "Patient reports a wound from battle. Destroyed massive armies and is struggling to regain strength.\n\nRecommend lots of wine, rest, and some light exercise.",
    },
)
