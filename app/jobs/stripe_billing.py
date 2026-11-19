"""Periodic repair for durable Stripe quantity-sync obligations."""

from app import log
from app.celery import celery_app
from app.services.stripe_billing import reconcile_pending_quantity_syncs


@celery_app.task
def perform() -> dict[str, int]:
    from app.services.invoice_billing import reconcile_invoice_quantities

    invoice_succeeded, invoice_failed = reconcile_invoice_quantities()
    succeeded, failed = reconcile_pending_quantity_syncs()
    succeeded += invoice_succeeded
    failed += invoice_failed
    log.info(
        "stripe quantity reconciliation completed",
        succeeded=succeeded,
        failed=failed,
    )
    return {"succeeded": succeeded, "failed": failed}
