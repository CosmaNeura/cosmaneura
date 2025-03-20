"""
Loop through all Doctors in our DB, retrieve the associated Clerk user,
and update the Doctor record's email column from the Clerk data.
"""

from app import log
from app.celery import celery
from app.configuration.clerk import clerk

from app.models.doctor import Doctor


def perform() -> None:
    """
    Perform the syncing of Doctor email addresses with Clerk user data.
    """

    # TODO support deleted flag in clerk

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

    log.info("users synced with clerk")


perform_celery = celery.task(perform)


def queue():
    "always use this method to queue jobs so it's easy to swap out the job system"
    perform_celery.delay()
