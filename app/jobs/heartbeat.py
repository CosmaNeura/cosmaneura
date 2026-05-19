"""Periodic no-op task whose sole purpose is to prove the celery worker
is alive and processing jobs.

Sentry's CeleryIntegration with monitor_beat_tasks=True auto-creates a
cron monitor for every entry in beat_schedule. If this task doesn't
fire on its schedule (because the worker is hung, crash-looping, or
silently disconnected from Redis) Sentry pages us — no separate alert
config needed beyond the existing Sentry project.

Today's incident (2026-05-19) went undetected for ~6 hours because we
had no signal between "container Running" (true) and "jobs processing"
(false). This closes that gap to ~1 minute of latency."""

from app import log
from app.celery import celery_app


def perform() -> None:
    log.info("worker heartbeat")


perform_celery = celery_app.task(name="app.jobs.heartbeat.perform")(perform)
