"""Celery entry points for the durable external-webhook inbox."""

from app.celery import celery_app
from app.services import external_webhook_inbox


def perform(delivery_id: str) -> None:
    """Process one internal inbox TypeID; no provider payload enters Redis."""

    external_webhook_inbox.process_delivery(delivery_id)


process_delivery_celery = celery_app.task(
    name="app.jobs.external_webhooks.process_delivery",
    autoretry_for=(),
    max_retries=0,
    time_limit=5 * 60,
)(perform)


def reconcile() -> None:
    external_webhook_inbox.reconcile()


reconcile_celery = celery_app.task(
    name="app.jobs.external_webhooks.reconcile",
    autoretry_for=(),
    max_retries=0,
    time_limit=60,
)(reconcile)


def dispatch_pipeline(operation_id: str) -> None:
    from app.services import recall_webhook_operations

    recall_webhook_operations.dispatch_pipeline(operation_id)


dispatch_pipeline_celery = celery_app.task(
    name="app.jobs.external_webhooks.dispatch_pipeline",
    autoretry_for=(),
    max_retries=0,
    time_limit=60,
)(dispatch_pipeline)
