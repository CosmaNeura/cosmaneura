"""Drain one durable preparation request without sending source text to Redis."""

import app.configuration.session_prep as configuration
from app import log
from app.celery import celery_app
from app.services.session_prep_alerts import (
    report_degraded_health,
    report_maintenance_unavailable,
)
from app.services.session_prep_operations import expire_read_receipts, runtime_health
from app.services.session_prep_worker import run_one_build


def perform():
    if not configuration.SESSION_PREP_PROVIDER_SETTINGS.enabled:
        return
    try:
        outcome = run_one_build()
    except Exception:
        # Database/SDK exceptions may carry bound source or response parameters.
        # The durable lease handles retry after this metadata-only failure.
        outcome = "storage_failed"
    if outcome != "idle":
        log.info(
            "Session Prep worker outcome",
            event_code="session_prep_build",
            outcome=outcome,
        )


perform_celery = celery_app.task(soft_time_limit=60, time_limit=75, ignore_result=True)(
    perform
)


def maintain():
    try:
        expired = expire_read_receipts()
        health = runtime_health()
    except Exception:
        log.warning(
            "Session Prep maintenance failed",
            event_code="session_prep_maintenance_failed",
        )
        report_maintenance_unavailable()
        return
    enabled = configuration.SESSION_PREP_PROVIDER_SETTINGS.enabled
    report = log.warning if enabled and health["status"] == "degraded" else log.info
    report(
        "Session Prep runtime health",
        event_code="session_prep_runtime_health",
        enabled=enabled,
        expired_receipts=expired,
        **health,
    )
    if enabled and health["status"] == "degraded":
        report_degraded_health(health)


maintain_celery = celery_app.task(time_limit=30, ignore_result=True)(maintain)
