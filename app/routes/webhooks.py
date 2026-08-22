import hashlib
import hmac
import json
import time
from datetime import timedelta
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

import app.jobs.stripe_events
import app.jobs.webhooks
import app.services.stripe_billing as stripe_billing
from app import log
from app.configuration import stripe as stripe_config
from app.configuration.redis import get_redis
from app.configuration.zoom import ZOOM_SECRET_TOKEN

webhook_api_app = APIRouter(prefix="/external/v1/webhooks", tags=["webhooks"])

# Signed Zoom webhooks are only valid for ~5 minutes; this bounds replay risk.
# https://developers.zoom.us/docs/api/webhooks/#validate-your-webhook-endpoint
_ZOOM_SIGNATURE_TTL_SECONDS = 5 * 60


def _verify_zoom_signature(
    *,
    raw_body: bytes,
    timestamp: str | None,
    signature: str | None,
) -> bool:
    """Verify the ``x-zm-signature`` header Zoom sends on every webhook.

    Zoom signs each request as ``v0=<hex>`` where the HMAC-SHA256 input is
    ``"v0:" + x-zm-request-timestamp + ":" + raw_body`` keyed by the
    app's Secret Token.
    """
    if not ZOOM_SECRET_TOKEN:
        log.warning(
            "zoom webhook received but ZOOM_SECRET_TOKEN is unset; "
            "skipping signature verification"
        )
        return True

    if not timestamp or not signature:
        return False

    try:
        request_ts = int(timestamp)
    except ValueError:
        return False

    if abs(int(time.time()) - request_ts) > _ZOOM_SIGNATURE_TTL_SECONDS:
        return False

    message = b"v0:" + timestamp.encode() + b":" + raw_body
    expected = (
        "v0="
        + hmac.new(
            ZOOM_SECRET_TOKEN.encode(),
            message,
            hashlib.sha256,
        ).hexdigest()
    )

    return hmac.compare_digest(expected, signature)


class WebhookAck(BaseModel):
    status: str
    duplicate: bool = False


def _stable_event_id(
    provider: str,
    payload: dict[str, Any],
    headers: dict[str, str],
) -> str:
    provider_event_id = (
        payload.get("event_id")
        or payload.get("id")
        or payload.get("uuid")
        or headers.get("x-zm-trackingid")
        or headers.get("x-request-id")
    )

    if provider_event_id:
        return str(provider_event_id)

    raw = json.dumps(payload, sort_keys=True)
    digest = hashlib.sha256(raw.encode()).hexdigest()
    return f"{provider}:{digest}"


def _webhook_dedupe_key(provider: str, event_id: str) -> str:
    return f"webhook:{provider}:{event_id}"


def _already_processed(provider: str, event_id: str) -> bool:
    redis = get_redis()

    # Stripe retries live-mode deliveries for up to three days. Redis is only
    # a cache for Stripe (the DB receipt is authoritative), but keep it longer
    # than that retry window to avoid needless remote refreshes.
    inserted = redis.set(
        _webhook_dedupe_key(provider, event_id), "1", nx=True, ex=timedelta(days=7)
    )
    return inserted is None


def _already_processed_probe(provider: str, event_id: str) -> bool:
    "check without marking — for handlers that mark only after success"
    return bool(get_redis().exists(_webhook_dedupe_key(provider, event_id)))


def _mark_processed(provider: str, event_id: str) -> None:
    get_redis().set(_webhook_dedupe_key(provider, event_id), "1", ex=timedelta(days=7))


async def _queue_webhook(
    provider: str, request: Request, payload: dict[str, Any] | None = None
) -> WebhookAck:
    if payload is None:
        payload = await request.json()
    assert payload is not None
    headers = {k.lower(): v for k, v in request.headers.items()}
    event_id = _stable_event_id(provider=provider, payload=payload, headers=headers)
    event_type = str(payload.get("event", payload.get("event_type", "unknown")))

    if _already_processed(provider=provider, event_id=event_id):
        log.info(
            "webhook already processed",
            provider=provider,
            event_id=event_id,
            event_type=event_type,
        )
        return WebhookAck(status="ok", duplicate=True)

    app.jobs.webhooks.queue(
        provider=provider,
        event_id=event_id,
        event_type=event_type,
        payload=payload,
        headers=headers,
    )

    return WebhookAck(status="ok")


@webhook_api_app.post("/zoom")
async def zoom_webhook(request: Request):
    raw_body = await request.body()
    timestamp = request.headers.get("x-zm-request-timestamp")
    signature = request.headers.get("x-zm-signature")

    if not _verify_zoom_signature(
        raw_body=raw_body, timestamp=timestamp, signature=signature
    ):
        log.warning(
            "zoom webhook signature verification failed",
            has_timestamp=bool(timestamp),
            has_signature=bool(signature),
        )
        return JSONResponse({"status": "invalid signature"}, status_code=401)

    payload = json.loads(raw_body)

    if payload.get("event") == "endpoint.url_validation":
        plain_token = payload["payload"]["plainToken"]
        encrypted_token = hmac.new(
            ZOOM_SECRET_TOKEN.encode(),
            plain_token.encode(),
            hashlib.sha256,
        ).hexdigest()
        log.info("zoom url validation challenge received")
        return JSONResponse(
            {"plainToken": plain_token, "encryptedToken": encrypted_token}
        )

    return await _queue_webhook(provider="zoom", request=request, payload=payload)


@webhook_api_app.post("/recall")
async def recall_webhook(request: Request) -> WebhookAck:
    return await _queue_webhook(provider="recall", request=request)


@webhook_api_app.post("/stripe")
async def stripe_webhook(request: Request, background_tasks: BackgroundTasks):
    """Mirror verified Stripe subscription events onto the practice.

    Entitlement lives on the platform (ADR 0017); Stripe events only set the
    mirrored ``subscription_status``/ids. Unverifiable events are never
    accepted: 503 with no secret configured, 401 on a bad signature.
    """
    raw_body = await request.body()

    if not stripe_config.stripe_webhook_configured():
        log.warning("stripe webhook received while billing rollout is not ready")
        return JSONResponse(
            {"status": "billing webhook not configured"}, status_code=503
        )

    if not stripe_billing.verify_webhook_signature(
        raw_body, request.headers.get("stripe-signature")
    ):
        log.warning("stripe webhook signature verification failed")
        return JSONResponse({"status": "invalid signature"}, status_code=401)

    try:
        event = json.loads(raw_body)
    except json.JSONDecodeError:
        return JSONResponse({"status": "invalid JSON"}, status_code=400)
    event_id = str(event.get("id") or "")
    event_type = str(event.get("type") or "unknown")

    # Persist the signed envelope before acknowledging it. Stripe HTTP calls
    # happen in Celery; a one-minute DB sweep recovers any broker publish loss.
    try:
        inbox_id, duplicate = await run_in_threadpool(
            stripe_billing.enqueue_stripe_event, event
        )
    except stripe_billing.StripeStateInvalid as exc:
        log.error(
            "stripe webhook state validation failed",
            event_id=event_id,
            event_type=event_type,
            error=str(exc),
        )
        return JSONResponse({"status": "invalid Stripe state"}, status_code=400)
    except Exception:
        log.warning(
            "stripe webhook could not persist its durable inbox record",
            event_id=event_id,
            event_type=event_type,
            exc_info=True,
        )
        return JSONResponse(
            {"status": "Stripe inbox temporarily unavailable"}, status_code=503
        )

    if inbox_id is not None and not duplicate:
        background_tasks.add_task(_publish_stripe_inbox, str(inbox_id), event_id)

    log.info(
        "stripe webhook durably accepted",
        event_id=event_id,
        event_type=event_type,
        inbox_id=str(inbox_id) if inbox_id is not None else None,
        duplicate=duplicate,
    )
    return WebhookAck(status="ok", duplicate=duplicate)


def _publish_stripe_inbox(inbox_id: str, event_id: str) -> None:
    """Best-effort post-response publish; the DB sweep is authoritative."""

    try:
        app.jobs.stripe_events.process.delay(inbox_id)
    except Exception:
        log.warning(
            "stripe event persisted but immediate worker publish failed; sweep will retry",
            event_id=event_id,
            inbox_id=inbox_id,
            exc_info=True,
        )
