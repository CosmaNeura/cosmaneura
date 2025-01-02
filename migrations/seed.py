# seed fresh development and test databases with data

from datetime import datetime

from app.models.patient import Patient
from app.models.doctor import Doctor
from app.models.appointment import Appointment
from app.models.visit_audio_file import VisitAudioFile

# create a doctor
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
    azure_file_name="teresa-jensen-mock-appointment.mp4"
)
