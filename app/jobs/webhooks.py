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
    log.info(
        "processing webhook event",
        provider=provider,
        event_id=event_id,
        event_type=event_type,
    )

    # phase 2 will dispatch to provider-specific command handlers.
    log.debug(
        "webhook payload received",
        provider=provider,
        event_id=event_id,
        payload=payload,
        headers=headers,
    )


perform_celery = celery_app.task(perform)


def queue(*args, **kwargs):
    log.info("queuing webhook event", args=args, kwargs=kwargs)
    perform_celery.delay(*args, **kwargs)
