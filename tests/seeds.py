# seed fresh test databases with data

from datetime import datetime

from app import log

from app.models.appointment import Appointment
from app.models.doctor import Doctor
from app.models.doctor_note_file import DoctorNoteFile
from app.models.patient import Patient
from app.models.visit_audio_file import VisitAudioFile

from tests.utils import get_clerk_dev_user

def seed_test_data():
    log.info("seeding data")

    username, password, user = get_clerk_dev_user()
    doctor = Doctor.find_or_create_by(clerk_id=user.id)

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
        azure_file_name="fiat-health-mock-patient-appointment-12-17-2024.m4a"
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
