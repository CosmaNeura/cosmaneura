"""
Create an intake directly from the provider UI, independent of any external
scheduling system sync (Acuity, calendar, etc.). This is what powers the
provider's one-click "send an intake to my patient" flow.
"""

from datetime import date, datetime

from app import log
from app.jobs.intake.generate_questions import queue as queue_question_generation

from app.models.appointment import Appointment
from app.models.doctor import Doctor
from app.models.intake.intake import Intake
from app.models.patient import Patient


def perform(
    *,
    doctor: Doctor,
    patient_name: str,
    date_of_birth: date,
    patient_email: str,
    appointment_date: datetime,
) -> Intake:
    # mirrors the external API flow (app.commands.intake.create): patients are
    # matched by name + DOB within a doctor to reduce duplicates
    patient = Patient.find_or_create_by(
        name=patient_name.strip(),
        date_of_birth=date_of_birth,
        doctor_id=doctor.id,
    )

    # always store the freshest contact information
    patient.email = patient_email
    patient.save()

    appointment = Appointment(
        doctor_id=doctor.id,
        patient_id=patient.id,
        appointment_date=appointment_date,
        external_id=None,
        created_by_system="gui",
    ).save()

    intake = Intake(
        doctor_id=doctor.id,
        appointment_id=appointment.id,
        intake_questions=[],
        external_intake_data={},
    ).save()

    log.info("created manual intake", intake_id=intake.id, doctor_id=doctor.id)

    queue_question_generation(intake.id)

    return intake
