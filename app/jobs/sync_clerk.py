"""
Loop through all Doctors in our DB, retrieve the associated Clerk user,
and update the Doctor record's email column from the Clerk data.
"""

from app import log
from app.celery import celery_app
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

        primary_id = clerk_user.primary_email_address_id
        primary = next(
            (e for e in clerk_user.email_addresses if e.id == primary_id),
            None,
        )
        if primary is None:
            log.warning(
                "primary email not in email_addresses for clerk user",
                doctor=doctor,
                primary_email_address_id=primary_id,
            )
            continue

        doctor_email = primary.email_address

        if doctor.email == doctor_email:
            continue

        existing = Doctor.get(email=doctor_email)
        if existing is not None and existing.id != doctor.id:
            log.warning(
                "email collision in clerk sync, skipping doctor",
                doctor=doctor,
                conflicting_doctor_id=existing.id,
                email=doctor_email,
            )
            continue

        doctor.email = doctor_email
        doctor.save()

    log.info("users synced with clerk")


perform_celery = celery_app.task(perform)


def queue():
    "always use this method to queue jobs so it's easy to swap out the job system"
    perform_celery.delay()
