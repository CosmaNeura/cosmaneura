"""
Job: sync_clerk.py

Loop through all Doctors in our DB, retrieve the associated Clerk user,
and update the Doctor record's email column from the Clerk data.
"""

from app import log
from app.configuration.clerk import clerk

from app.models.doctor import Doctor


def perform() -> None:
    """
    Perform the syncing of Doctor email addresses with Clerk user data.
    """

    for doctor in Doctor.all():
        try:
            clerk_user = clerk.users.get(user_id=doctor.clerk_id)
        except Exception:
            log.warning("no clerk user found for doctor", doctor=doctor)
            continue

        assert clerk_user

        if not clerk_user.email_addresses:
            log.warning("no email addresses found for clerk user", doctor=doctor)
            continue

        if len(clerk_user.email_addresses) > 1:
            log.info("multiple email addresses found for clerk user", doctor=doctor)

        doctor_email = clerk_user.email_addresses[0].email_address

        doctor.email = doctor_email
        doctor.save()


def queue() -> None:
    """
    Queue this job for background processing.

    Patterned off the perform & queue methods from extract.py.
    Replace the enqueue call with your project's job scheduler integration.
    """
    try:
        from app.jobs.job_queue import (
            enqueue_job,  # Adjust the import based on your project structure.
        )
    except ImportError:
        log.error("Job scheduler module not found. Cannot queue job.")
        return

    # Enqueue the perform function as a background job.
    enqueue_job(perform)
    log.info("Queued sync_clerk job.")
