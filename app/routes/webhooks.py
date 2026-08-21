"""Fail-closed, bounded ingress for signed Recall and Zoom webhooks."""

import base64
import binascii
import hashlib
import hmac
import json
import re
import time
from typing import Any, Literal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app import log
from app.configuration.webhooks import (
    DURABLE_WEBHOOK_INBOX_ENABLED,
    RECALL_WEBHOOK_SECRET,
)
from app.configuration.zoom import ZOOM_SECRET_TOKEN
from app.services import external_webhook_inbox

webhook_api_app = APIRouter(prefix="/external/v1/webhooks", tags=["webhooks"])

_SIGNATURE_TTL_SECONDS = 5 * 60
MAX_WEBHOOK_BODY_BYTES = 256 * 1024
_EVENT_TYPE_RE = re.compile(r"^[A-Za-z0-9_.-]{1,96}$")
RECALL_SUPPORTED_EVENT_TYPES = frozenset(
    {
        "recording.done",
        "transcript.done",
        "transcript.failed",
        "transcript.processing",
    }
)


class WebhookAck(BaseModel):
    status: str
    duplicate: bool = False


class WebhookBodyTooLarge(Exception):
    pass


class InvalidContentLength(Exception):
    pass


async def _read_bounded_body(request: Request) -> bytes:
    """Read a request without allowing Starlette to buffer an unbounded body."""

    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            declared = int(content_length)
        except ValueError as exc:
            raise InvalidContentLength from exc
        if declared < 0:
            raise InvalidContentLength
        if declared > MAX_WEBHOOK_BODY_BYTES:
            raise WebhookBodyTooLarge

    body = bytearray()
    async for chunk in request.stream():
        if len(chunk) > MAX_WEBHOOK_BODY_BYTES - len(body):
            raise WebhookBodyTooLarge
        body.extend(chunk)
    return bytes(body)


def _verify_zoom_signature(
    *,
    raw_body: bytes,
    timestamp: str | None,
    signature: str | None,
) -> bool:
    """Verify Zoom's ``v0:<timestamp>:<raw body>`` HMAC, fail closed."""

    if not ZOOM_SECRET_TOKEN or not timestamp or not signature:
        return False
    try:
        request_ts = int(timestamp)
        timestamp_bytes = timestamp.encode("ascii")
    except (ValueError, UnicodeEncodeError):
        return False
    if abs(int(time.time()) - request_ts) > _SIGNATURE_TTL_SECONDS:
        return False
    message = b"v0:" + timestamp_bytes + b":" + raw_body
    expected = (
        "v0="
        + hmac.new(
            ZOOM_SECRET_TOKEN.encode("utf-8"), message, hashlib.sha256
        ).hexdigest()
    )
    return hmac.compare_digest(expected, signature)


def _recall_signing_key(secret: str) -> bytes | None:
    if not secret.startswith("whsec_"):
        return None
    try:
        key = base64.b64decode(secret.removeprefix("whsec_"), validate=True)
    except (binascii.Error, ValueError):
        return None
    return key or None


def _verify_recall_signature(
    *,
    raw_body: bytes,
    webhook_id: str | None,
    timestamp: str | None,
    signature: str | None,
    secret: str | None = None,
) -> Literal["valid", "invalid", "unconfigured"]:
    """Verify Recall/Svix HMAC over the exact UTF-8 request bytes.

    Recall accepts both its current ``webhook-*`` header names and legacy
    ``svix-*`` names. Multiple space-separated ``v1,<signature>`` values are
    supported during key rotation.
    """

    signing_key = _recall_signing_key(
        RECALL_WEBHOOK_SECRET if secret is None else secret
    )
    if signing_key is None:
        return "unconfigured"
    if not webhook_id or not timestamp or not signature:
        return "invalid"
    try:
        request_ts = int(timestamp)
        body_text = raw_body.decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return "invalid"
    if abs(int(time.time()) - request_ts) > _SIGNATURE_TTL_SECONDS:
        return "invalid"

    signed = f"{webhook_id}.{timestamp}.{body_text}".encode("utf-8")
    expected = base64.b64encode(
        hmac.new(signing_key, signed, hashlib.sha256).digest()
    ).decode("ascii")
    for candidate in signature.split():
        version, separator, value = candidate.partition(",")
        if separator and version == "v1" and hmac.compare_digest(expected, value):
            return "valid"
    return "invalid"


def _header_alias(request: Request, current: str, legacy: str) -> str | None:
    return request.headers.get(current) or request.headers.get(legacy)


def _decode_json_object(raw_body: bytes) -> dict[str, Any] | None:
    try:
        value = json.loads(raw_body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _event_type(payload: dict[str, Any]) -> str | None:
    value = payload.get("event") or payload.get("event_type")
    if not isinstance(value, str) or not _EVENT_TYPE_RE.fullmatch(value):
        return None
    return value


def _durable_ack(
    *,
    provider: Literal["recall", "zoom"],
    provider_event_id: str,
    event_type: str,
    payload: dict[str, Any],
) -> WebhookAck | JSONResponse:
    if not DURABLE_WEBHOOK_INBOX_ENABLED:
        # Release-A compatibility: keep the existing signed queue path live
        # while the migration, compatible workers and scheduler roll out. The
        # broker receives a keyed pseudonym instead of the raw provider ID and
        # no request headers. Its compatibility consumer converges into the
        # durable inbox before invoking a handler.
        from app.jobs import webhooks as legacy_webhooks

        legacy_webhooks.queue(
            provider=provider,
            event_id=external_webhook_inbox.queued_provider_event_reference(
                provider_event_id
            ),
            event_type=event_type,
            payload=payload,
            headers={},
        )
        return WebhookAck(status="ok")
    try:
        stored = external_webhook_inbox.store_verified_external_webhook(
            provider=provider,
            provider_event_id=provider_event_id,
            event_type=event_type,
            payload=payload,
        )
    except external_webhook_inbox.WebhookConflictError:
        return JSONResponse({"status": "conflict"}, status_code=409)
    external_webhook_inbox.publish_best_effort(stored.delivery_id)
    return WebhookAck(status="ok", duplicate=stored.duplicate)


@webhook_api_app.post("/zoom", response_model=None)
async def zoom_webhook(request: Request) -> WebhookAck | JSONResponse:
    if not ZOOM_SECRET_TOKEN:
        log.error("zoom webhook unavailable: signing secret not configured")
        return JSONResponse({"status": "unavailable"}, status_code=503)
    try:
        raw_body = await _read_bounded_body(request)
    except WebhookBodyTooLarge:
        return JSONResponse({"status": "payload too large"}, status_code=413)
    except InvalidContentLength:
        return JSONResponse({"status": "invalid request"}, status_code=400)

    timestamp = request.headers.get("x-zm-request-timestamp")
    signature = request.headers.get("x-zm-signature")
    if not _verify_zoom_signature(
        raw_body=raw_body, timestamp=timestamp, signature=signature
    ):
        log.warning("zoom webhook signature verification failed")
        return JSONResponse({"status": "invalid signature"}, status_code=401)

    payload = _decode_json_object(raw_body)
    if payload is None:
        return JSONResponse({"status": "invalid payload"}, status_code=400)

    if payload.get("event") == "endpoint.url_validation":
        payload_value = payload.get("payload")
        plain_token = (
            payload_value.get("plainToken") if isinstance(payload_value, dict) else None
        )
        if not isinstance(plain_token, str):
            return JSONResponse({"status": "invalid payload"}, status_code=400)
        encrypted_token = hmac.new(
            ZOOM_SECRET_TOKEN.encode("utf-8"),
            plain_token.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        log.info("zoom url validation challenge received")
        return JSONResponse(
            {"plainToken": plain_token, "encryptedToken": encrypted_token}
        )

    event_type = _event_type(payload)
    if event_type is None:
        return JSONResponse({"status": "invalid payload"}, status_code=400)
    tracking_id = request.headers.get("x-zm-trackingid")
    provider_event_id = external_webhook_inbox.stable_event_reference(
        "zoom", payload, tracking_id
    )
    return _durable_ack(
        provider="zoom",
        provider_event_id=provider_event_id,
        event_type=event_type,
        payload=payload,
    )


@webhook_api_app.post("/recall", response_model=None)
async def recall_webhook(request: Request) -> WebhookAck | JSONResponse:
    if _recall_signing_key(RECALL_WEBHOOK_SECRET) is None:
        log.error("recall webhook unavailable: signing secret not configured")
        return JSONResponse({"status": "unavailable"}, status_code=503)
    try:
        raw_body = await _read_bounded_body(request)
    except WebhookBodyTooLarge:
        return JSONResponse({"status": "payload too large"}, status_code=413)
    except InvalidContentLength:
        return JSONResponse({"status": "invalid request"}, status_code=400)

    webhook_id = _header_alias(request, "webhook-id", "svix-id")
    timestamp = _header_alias(request, "webhook-timestamp", "svix-timestamp")
    signature = _header_alias(request, "webhook-signature", "svix-signature")
    verification = _verify_recall_signature(
        raw_body=raw_body,
        webhook_id=webhook_id,
        timestamp=timestamp,
        signature=signature,
    )
    if verification != "valid":
        log.warning("recall webhook signature verification failed")
        return JSONResponse({"status": "invalid signature"}, status_code=401)

    payload = _decode_json_object(raw_body)
    if payload is None:
        return JSONResponse({"status": "invalid payload"}, status_code=400)
    event_type = _event_type(payload)
    if event_type is None:
        return JSONResponse({"status": "invalid payload"}, status_code=400)
    if event_type not in RECALL_SUPPORTED_EVENT_TYPES:
        # Desktop SDK uploads require a durable, server-authoritative capture
        # to encounter/tenant binding. Until that adapter exists, reject new
        # subscriptions explicitly instead of acknowledging and dropping them
        # or falling back to the Zoom bot's time-window appointment heuristic.
        log.warning("unsupported recall webhook event type")
        return JSONResponse({"status": "unsupported event"}, status_code=400)
    assert webhook_id is not None  # required by successful verification
    return _durable_ack(
        provider="recall",
        provider_event_id=webhook_id,
        event_type=event_type,
        payload=payload,
    )
