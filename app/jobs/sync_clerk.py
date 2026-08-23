"""
Loop through all Doctors in our DB, retrieve the associated Clerk user,
and update the Doctor record's email column from the Clerk data.
"""

from app import log
from app.celery import celery_app
from app.configuration.clerk import clerk
from app.services.external_webhook_inbox import provider_log_ref

from app.models.doctor import Doctor

_MAX_LOG_COUNT = 10_000


def _bounded_count(value: int) -> int:
    return min(value, _MAX_LOG_COUNT)


def _doctor_ref(doctor_id: object) -> str:
    return provider_log_ref(f"doctor:{doctor_id}")


def perform() -> None:
    """
    Perform the syncing of Doctor email addresses with Clerk user data.
    """

    # TODO support deleted flag in clerk

    processed = 0
    synced = 0
    skipped_missing_user = 0
    skipped_missing_email = 0
    skipped_missing_primary = 0
    skipped_collision = 0

    for doctor in Doctor.all():
        processed += 1
        doctor_ref = _doctor_ref(doctor.id)
        try:
            clerk_user = clerk.users.get(user_id=doctor.clerk_id)
        except Exception:
            skipped_missing_user += 1
            log.warning(
                "clerk sync record skipped",
                event_code="clerk_user_lookup_failed",
                doctor_ref=doctor_ref,
            )
            continue

        assert clerk_user

        if not clerk_user.email_addresses:
            skipped_missing_email += 1
            log.warning(
                "clerk sync record skipped",
                event_code="clerk_email_missing",
                doctor_ref=doctor_ref,
            )
            continue

        primary_id = clerk_user.primary_email_address_id
        primary = next(
            (e for e in clerk_user.email_addresses if e.id == primary_id),
            None,
        )
        if primary is None:
            skipped_missing_primary += 1
            log.warning(
                "clerk sync record skipped",
                event_code="clerk_primary_email_missing",
                doctor_ref=doctor_ref,
                email_ref=provider_log_ref(f"clerk-email:{primary_id}"),
            )
            continue

        doctor_email = primary.email_address

        if doctor.email == doctor_email:
            continue

        existing = Doctor.get(email=doctor_email)
        if existing is not None and existing.id != doctor.id:
            skipped_collision += 1
            log.warning(
                "clerk sync record skipped",
                event_code="clerk_email_collision",
                doctor_ref=doctor_ref,
                conflicting_doctor_ref=_doctor_ref(existing.id),
                email_ref=provider_log_ref(f"email:{doctor_email.strip().casefold()}"),
            )
            continue

        doctor.email = doctor_email
        doctor.save()
        synced += 1

    log.info(
        "clerk user sync completed",
        event_code="clerk_sync_completed",
        processed=_bounded_count(processed),
        synced=_bounded_count(synced),
        skipped_missing_user=_bounded_count(skipped_missing_user),
        skipped_missing_email=_bounded_count(skipped_missing_email),
        skipped_missing_primary=_bounded_count(skipped_missing_primary),
        skipped_collision=_bounded_count(skipped_collision),
    )


perform_celery = celery_app.task(perform)


def queue():
    "always use this method to queue jobs so it's easy to swap out the job system"
    perform_celery.delay()
