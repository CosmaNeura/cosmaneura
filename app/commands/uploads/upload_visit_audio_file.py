from datetime import datetime

import app.jobs.transcribe
from app import log

from app.models.appointment import Appointment
from app.models.doctor import Doctor
from app.models.patient import Patient
from app.models.visit_audio_file import VisitAudioFile


def perform(
    *,
    doctor: Doctor,
    file_name: str,
    patient_name: str,
    appointment_date: datetime,
    date_of_birth: datetime,
) -> str:
    """
    Handles processing a doctor uploading a visit audio file for an appointment
    """

    # Registration can be retried if the API process loses its response after
    # durable writes. The server-generated blob key is the idempotency key;
    # never create a second patient/appointment/file for the same upload.
    existing_file = VisitAudioFile.get(
        doctor_id=doctor.id,
        azure_file_name=file_name,
    )
    if existing_file is not None:
        log.info("resuming registered audio upload", id=existing_file.id)
        app.jobs.transcribe.queue(existing_file.id)
        return str(existing_file.appointment_id)

    # TODO wrap in transaction, needs to be added to activemodel

    # only care about the date, we want to optimize for matching
    normalized_date_of_birth = date_of_birth.replace(
        hour=0, minute=0, second=0, microsecond=0
    )

    patient = Patient.find_or_create_by(
        doctor_id=doctor.id, name=patient_name, date_of_birth=normalized_date_of_birth
    )

    appointment = Appointment(
        doctor_id=doctor.id,
        patient_id=patient.id,
        appointment_date=appointment_date,
        external_id=None,
    ).save()

    file = VisitAudioFile(
        doctor_id=doctor.id,
        patient_id=patient.id,
        appointment_id=appointment.id,
        azure_file_name=file_name,
    ).save()

    log.info("processing audio file", id=file.id)

    app.jobs.transcribe.queue(file.id)
    return str(appointment.id)
