"""Default-off, argument-free queue drain; no answers or credentials in Redis."""

import app.configuration.visit_prep as configuration
from app import log
from app.celery import celery_app
from app.services.visit_prep_synthesis_operations import runtime_health
from app.services.visit_prep_synthesis_worker import run_one_build


def perform():
    settings = configuration.VISIT_PREP_SETTINGS
    if not settings.enabled or not settings.configuration_valid:
        return
    try:
        outcome = run_one_build()
    except Exception:
        # Keep bound SQL/SDK parameters out of logging and task failure stores.
        # The durable lease permits bounded recovery after an interrupted run.
        outcome = "storage_failed"
    if outcome != "idle":
        log.info(
            "Visit Prep synthesis worker outcome",
            event_code="visit_prep_synthesis_build",
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
    settings = configuration.VISIT_PREP_SETTINGS
    if not settings.enabled or not settings.configuration_valid:
        return
    try:
        health = runtime_health()
    except Exception:
        log.warning(
            "Visit Prep synthesis maintenance failed",
            event_code="visit_prep_synthesis_maintenance_failed",
        )
        return
    report = log.warning if health["status"] == "degraded" else log.info
    report(
        "Visit Prep synthesis runtime health",
        event_code="visit_prep_synthesis_runtime_health",
        **health,
    )


maintain_celery = celery_app.task(
    time_limit=30, ignore_result=True, autoretry_for=(), max_retries=0
)(maintain)
