"""
Resolve which email address to use and queue the intake link email.
"""

from app import log
from app.jobs.intake.send_link import queue as queue_send_link

from app.models.intake.intake import Intake
from app.models.patient import Patient


def perform(intake: Intake, email: str | None = None) -> str:
    """
    Queue the intake link email for the intake's patient.

    When `email` is provided it is saved on the patient record so future sends
    do not need to re-enter it; otherwise the patient's stored email is used.
    Returns the address the email was sent to.

    Raises ValueError when no email address is available.
    """
    patient = Patient.one(id=intake.appointment.patient_id)

    if email:
        patient.email = email
        patient.save()

    to_email = email or patient.email

    if not to_email:
        raise ValueError("No email address on file for this patient")

    log.info("queueing intake link email", intake_id=intake.id)
    queue_send_link(intake.id, to_email)

    return to_email
