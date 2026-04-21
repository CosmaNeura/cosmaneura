import hashlib
import hmac
import json
import time
from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import app.jobs.webhooks
from app import log
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
    expected = "v0=" + hmac.new(
        ZOOM_SECRET_TOKEN.encode(),
        message,
        hashlib.sha256,
    ).hexdigest()

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


def _already_processed(provider: str, event_id: str) -> bool:
    key = f"webhook:{provider}:{event_id}"
    redis = get_redis()

    # 48h is enough for provider retries while keeping keyspace bounded.
    inserted = redis.set(key, "1", nx=True, ex=timedelta(hours=48))
    return inserted is None


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
