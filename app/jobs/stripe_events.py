"""Immediate and periodic processing for the durable Stripe event inbox."""

from app import log
from app.celery import celery_app
from app.services.stripe_billing import (
    process_stripe_event_inbox,
    reconcile_pending_stripe_events,
)


@celery_app.task
def process(inbox_id: str) -> bool:
    return process_stripe_event_inbox(inbox_id)


@celery_app.task
def perform() -> dict[str, int]:
    succeeded, failed = reconcile_pending_stripe_events()
    log.info(
        "stripe event inbox reconciliation completed",
        succeeded=succeeded,
        failed=failed,
    )
    return {"succeeded": succeeded, "failed": failed}
