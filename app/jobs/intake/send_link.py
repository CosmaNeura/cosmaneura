"""
Email a patient the link to their intake chat.
"""

from typeid import TypeID

from app import log
from app.celery import celery_app
from app.configuration.emailer import mail

from activemodel.types import TypeIDType
from app.models.appointment import Appointment
from app.models.intake.intake import Intake
from app.models.patient import Patient


def perform(intake_id: str | TypeID | TypeIDType, email: str):
    """
    Render and send the intake link email. The email intentionally contains the
    minimum information needed (name, appointment date, link) and no other
    intake or clinical details.
    """
    log.info("sending intake link email", intake_id=intake_id)

    intake = Intake.one(id=intake_id)
    appointment = Appointment.one(id=intake.appointment_id)
    patient = Patient.one(id=appointment.patient_id)

    mail(
        to=email,
        subject="Please complete your appointment intake",
        template_path="mail/intake_link.md",
        context={
            "patient_name": patient.name,
            "appointment_date": appointment.appointment_date.strftime("%A, %B %-d, %Y"),
            "intake_url": str(intake.public_url),
        },
    )

    log.info("sent intake link email", intake_id=intake_id)


perform_celery = celery_app.task(perform)


def queue(*args, **kwargs):
    "always use this method to queue jobs so it's easy to swap out the job system"
    perform_celery.delay(*args, **kwargs)
