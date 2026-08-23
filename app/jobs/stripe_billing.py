"""Periodic repair for durable Stripe quantity-sync obligations."""

from app import log
from app.celery import celery_app
from app.services.stripe_billing import reconcile_pending_quantity_syncs


@celery_app.task
def perform() -> dict[str, int]:
    succeeded, failed = reconcile_pending_quantity_syncs()
    log.info(
        "stripe quantity reconciliation completed",
        succeeded=succeeded,
        failed=failed,
    )
    return {"succeeded": succeeded, "failed": failed}
