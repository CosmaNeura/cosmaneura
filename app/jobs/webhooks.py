from typing import Any

from app import log
from app.celery import celery_app


def perform(
    provider: str,
    event_id: str,
    event_type: str,
    payload: dict[str, Any],
    headers: dict[str, str],
):
    """Compatibility bridge for tasks queued before the durable cutover.

    Legacy messages already contain payload and headers in Redis.  They are
    normalized into the PostgreSQL inbox before any handler runs.  Older
    Recall tasks did not retain the signed delivery ID, so they cannot always
    converge at the inbox-row key with a later signed retry; the downstream
    provider-operation/canonical-ingest receipts are the second idempotency
    fence for that rolling-deploy case. Headers are intentionally ignored and
    never persisted or logged.
    """

    from app.services import external_webhook_inbox

    stored = external_webhook_inbox.store_legacy_queued_webhook(
        provider=provider,
        event_id=event_id,
        event_type=event_type,
        payload=payload,
    )
    external_webhook_inbox.process_delivery(stored.delivery_id)


perform_celery = celery_app.task(perform)


def queue(*args, **kwargs):
    # Never log task args/kwargs: legacy tasks contain provider payloads and
    # headers.  This method remains only for rolling-deploy compatibility.
    log.info("queuing legacy webhook event")
    perform_celery.delay(*args, **kwargs)
