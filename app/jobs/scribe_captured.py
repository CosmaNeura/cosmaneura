"""Bounded no-argument pickup through the existing Core scheduler/worker."""

from celery import Task

from app import log
from app.celery import celery_app
from app.scribe_bundle import perform_captured_pickup


def perform() -> None:
    outcome = perform_captured_pickup()
    if outcome != "disabled":
        log.info(
            "Scribe captured pickup",
            event_code="scribe_core_captured_pickup",
            outcome=outcome,
        )


perform_celery = celery_app.task(
    name="app.jobs.scribe_captured.perform",
    base=Task,
    autoretry_for=(),
    max_retries=0,
    soft_time_limit=370,
    time_limit=390,
    ignore_result=True,
)(perform)
