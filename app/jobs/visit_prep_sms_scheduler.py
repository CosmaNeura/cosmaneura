"""Argument-free queue planning. Missing trusted SMS sources keeps it dormant."""

from app import log
from app.celery import celery_app
from app.constants import INTAKE_CHAT_DOMAIN
from app.services.visit_prep_sms_scheduler import COUNT_FIELDS, schedule_sms_batch

# Exact source adapters must be installed and operationally qualified separately.
# No environment boolean or browser choice is a substitute for those verifiers.
SENDER_SOURCE_VERIFIER = None
CONSENT_SOURCE_VERIFIER = None


def _count(value: object) -> bool:
    return type(value) is int and 0 <= value <= 10


def perform():
    try:
        result = schedule_sms_batch(
            patient_origin=f"https://{INTAKE_CHAT_DOMAIN}",
            sender_source_verifier=SENDER_SOURCE_VERIFIER,
            consent_verifier=CONSENT_SOURCE_VERIFIER,
        )
        if result in (
            {"status": "disabled"},
            {"status": "busy"},
            {"status": "storage_unavailable"},
        ):
            metadata = result
        elif (
            set(result) == {"status", *COUNT_FIELDS}
            and result["status"] == "planned"
            and all(_count(result[name]) for name in COUNT_FIELDS)
            and result["scanned"]
            == sum(
                value
                for name in COUNT_FIELDS[1:]
                if isinstance(value := result[name], int)
            )
        ):
            metadata = result
        else:
            raise ValueError("unavailable")
    except Exception:
        metadata = {"status": "storage_unavailable"}
    if metadata != {"status": "disabled"}:
        log.info(
            "Visit Prep SMS planning", event_code="visit_prep_sms_planning", **metadata
        )


perform_celery = celery_app.task(
    time_limit=60, ignore_result=True, autoretry_for=(), max_retries=0
)(perform)
