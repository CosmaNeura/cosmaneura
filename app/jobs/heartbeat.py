"""Content-free heartbeat task for the existing beat/broker/worker path.

Global Sentry's Celery monitor applies only when its integration is enabled.
The separate staging health opt-in sends one fixed, unbound check-in without
enabling that global integration. External detection requires verified Sentry
ingestion, monitor activation and alert delivery; the task log alone is not it.
"""

from app import log
from app.celery import celery_app
from app.configuration.session_prep_health_alerts import capture_worker_heartbeat
from app.configuration.session_prep_production_heartbeat import (
    capture_production_worker_heartbeat,
)


def perform() -> None:
    log.info("worker heartbeat")
    capture_worker_heartbeat()
    capture_production_worker_heartbeat()


perform_celery = celery_app.task(name="app.jobs.heartbeat.perform")(perform)
