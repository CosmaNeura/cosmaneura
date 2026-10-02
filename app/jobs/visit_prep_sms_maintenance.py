"""Argument-free secret cleanup and metadata sampling, with no SMS send path."""

from app import log
from app.celery import celery_app
from app.services.visit_prep_sms_operations import COUNT_FIELDS, runtime_health
from app.services.visit_prep_sms_worker import maintain_sms_secrets


def _count(value: object) -> bool:
    return type(value) is int and value >= 0


def perform():
    # Disabled sending, missing source qualification and withdrawn BAA must not
    # preserve transient link/contact ciphertext. Retained records are not purged.
    try:
        result = maintain_sms_secrets()
        if (
            set(result) != {"status", "cleared"}
            or result["status"] not in {"available", "storage_unavailable"}
            or type(result["cleared"]) is not int
            or not 0 <= result["cleared"] <= 25
        ):
            raise ValueError("unavailable")
        cleanup = {"cleanup_status": result["status"], "cleared": result["cleared"]}
    except Exception:
        cleanup = {"cleanup_status": "storage_unavailable"}
    try:
        result = runtime_health()
        if result == {"status": "storage_unavailable"}:
            health = result
        elif (
            set(result) == {*COUNT_FIELDS, "status", "queue_timing"}
            and result["status"] in {"ok", "degraded"}
            and result["queue_timing"] == "unqualified"
            and all(_count(result[name]) for name in COUNT_FIELDS)
        ):
            health = result
        else:
            raise ValueError("unavailable")
    except Exception:
        health = {"status": "storage_unavailable"}
    log.info(
        "Visit Prep SMS maintenance",
        event_code="visit_prep_sms_maintenance",
        **cleanup,
        **health,
    )


perform_celery = celery_app.task(
    time_limit=30, ignore_result=True, autoretry_for=(), max_retries=0
)(perform)
