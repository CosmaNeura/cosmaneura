"""Argument-free outbox drain; never put recipients or invitation keys in Redis."""

from app import log
from app.celery import celery_app
from app.configuration import visit_prep, visit_prep_email_source
from app.configuration.visit_prep_email import CoreSMTPInvitationTransport
from app.constants import INTAKE_CHAT_DOMAIN
from app.services.visit_prep_email_source import verify_patient_email_source
from app.services.visit_prep_email_worker import (
    maintain_email_secrets,
    run_one_standard_email,
)


def perform():
    settings = visit_prep.VISIT_PREP_SETTINGS
    if not (
        settings.configuration_valid
        and settings.enabled
        and settings.public_api_enabled
        and settings.email_send_enabled
        and visit_prep_email_source.PATIENT_EMAIL_SOURCE_QUALIFIED is True
    ):
        return
    try:
        # Resolve transport BEFORE claiming. An unavailable SMTP configuration
        # must not consume a queued invitation's sole submission lane.
        transport = CoreSMTPInvitationTransport.from_configured_mailer()
        outcome = run_one_standard_email(
            patient_origin=f"https://{INTAKE_CHAT_DOMAIN}",
            transport=transport,
            source_verifier=verify_patient_email_source,
            settings=settings,
        )
    except Exception:
        outcome = "runtime_unavailable"
    if outcome != "idle":
        log.info(
            "Visit Prep invitation worker outcome",
            event_code="visit_prep_email_worker",
            outcome=outcome,
        )


perform_celery = celery_app.task(
    soft_time_limit=60,
    time_limit=75,
    ignore_result=True,
    autoretry_for=(),
    max_retries=0,
)(perform)


def maintain():
    # Even after deactivation, transient expired/obsolete invitation ciphertext
    # must be cleared. This does not purge retained consent or patient content.
    try:
        health = maintain_email_secrets()
    except Exception:
        health = {"status": "storage_unavailable", "suppressed": 0, "uncertain": 0}
    log.info(
        "Visit Prep invitation maintenance",
        event_code="visit_prep_email_maintenance",
        **health,
    )


maintain_celery = celery_app.task(
    time_limit=30, ignore_result=True, autoretry_for=(), max_retries=0
)(maintain)
