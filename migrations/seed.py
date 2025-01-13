# seed fresh development databases with data

from datetime import datetime

from app.models.ai_visit_note import AIVisitNote
from app.models.ai_visit_transcript import AIVisitTranscript, TranscriptEntry
from app.models.appointment import Appointment
from app.models.doctor import Doctor
from app.models.doctor_note_file import DoctorNoteFile
from app.models.doctor_note_schema import DoctorNoteSchema
from app.models.patient import Patient
from app.models.visit_audio_file import VisitAudioFile

doctor = Doctor.find_or_create_by(clerk_id="user_2pB8MHVdVjSxOye98WwpMHalwlT")

patient = Patient.find_or_create_by(
    doctor_id=doctor.id,
    name="Ghengis Khan",
    date_of_birth=datetime.strptime("1970-10-31", "%Y-%m-%d"),
)

appointment = Appointment.find_or_create_by(
    doctor_id=doctor.id,
    patient_id=patient.id,
    appointment_date=datetime.now(),
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
    ],
)

doctor_note_schema = DoctorNoteSchema.find_or_create_by(
    doctor_id=doctor.id,
    note_type="Telehealth SOAP Note",
    fields={
        "Chief Complaint": "Document the patient's primary reason for visit",
        "History of Present Illness": "Describe the chronological history of the current medical problem",
        "Assessment": "List the diagnoses and clinical impressions",
        "Plan": "Detail the treatment plan and next steps",
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
        "Plan": "Further evaluation needed to determine cause and appropriate treatment plan",
    },
)
