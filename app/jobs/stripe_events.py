"""Immediate and periodic processing for the durable Stripe event inbox."""

from app import log
from app.celery import celery_app
from app.services.stripe_billing import (
    StripeEventProcessingDeferred,
    process_stripe_event_inbox,
    reconcile_pending_stripe_events,
)


@celery_app.task
def process(inbox_id: str) -> bool:
    try:
        return process_stripe_event_inbox(inbox_id)
    except StripeEventProcessingDeferred:
        raise
    except Exception:
        raise StripeEventProcessingDeferred(
            "Stripe event processing is deferred for reconciliation"
        ) from None


@celery_app.task
def perform() -> dict[str, int]:
    try:
        succeeded, failed = reconcile_pending_stripe_events()
    except StripeEventProcessingDeferred:
        raise
    except Exception:
        raise StripeEventProcessingDeferred(
            "Stripe event processing is deferred for reconciliation"
        ) from None
    log.info(
        "stripe event inbox reconciliation completed",
        succeeded=succeeded,
        failed=failed,
    )
    return {"succeeded": succeeded, "failed": failed}
