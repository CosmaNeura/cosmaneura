from typing import Any

import app.jobs.recall_webhooks
import app.jobs.zoom_webhooks
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

    log.debug(
        "webhook payload received",
        provider=provider,
        event_id=event_id,
        payload=payload,
        headers=headers,
    )

    if provider == "zoom":
        app.jobs.zoom_webhooks.dispatch(event_type=event_type, payload=payload)
    elif provider == "recall":
        app.jobs.recall_webhooks.dispatch(event_type=event_type, payload=payload)


perform_celery = celery_app.task(perform)


def queue(*args, **kwargs):
    log.info("queuing webhook event", args=args, kwargs=kwargs)
    perform_celery.delay(*args, **kwargs)
