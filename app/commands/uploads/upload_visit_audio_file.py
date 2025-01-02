from datetime import datetime

from app import log

from app.models.appointment import Appointment
from app.models.doctor import Doctor
from app.models.patient import Patient
from app.models.visit_audio_file import VisitAudioFile


def perform(
    *,
    doctor: Doctor,
    blob_name: str,
    patient_name: str,
    appointment_date: datetime,
    date_of_birth: datetime,
) -> None:
    """
    Handles processing a doctor uploading a visit audio file for an appointment
    """

    # TODO wrap in transaction, needs to be addd to activemodel

    # TODO DOB + user_id filtering
    # Patient.select().where(Patient.name == patient_name).first()
    # TODO create patient if not exists

    patient = Patient(
        doctor_id=doctor.id, name=patient_name, date_of_birth=date_of_birth
    ).save()

    appointment = Appointment(
        doctor_id=doctor.id,
        patient_id=patient.id,
        appointment_date=appointment_date,
    ).save()

    file = VisitAudioFile(
        doctor_id=doctor.id,
        patient_id=patient.id,
        appointment_id=appointment.id,
        azure_file_name=blob_name,
    ).save()

    # TODO kick off job with the file.id
    log.info("processing audio file", id=file.id)
