"""Probe dependencies and ingress without claiming Marketplace-secret proof."""

import hashlib
import hmac
import json
import time

import httpx

from app import log
from app.celery import celery_app
from app.configuration.redis import get_redis
from app.configuration.server import API_BASE_URL
from app.configuration.zoom import ZOOM_SECRET_TOKEN
from app.environments import is_production, is_staging
from app.services import alerting, recall, zoom_webhooks

SYNTHETIC_HOST_ID = "synthetic-monitor-not-a-real-zoom-host"
SYNTHETIC_MEETING_ID = "synthetic-monitor-meeting-id"
SYNTHETIC_HTTP_MEETING_ID = "synthetic-monitor-http-meeting-id"

_PROBE_TIMEOUT = httpx.Timeout(15.0, connect=5.0)


def _payload(meeting_id: str) -> dict:
    return {
        "event": "meeting.started",
        "event_ts": int(time.time() * 1000),
        "payload": {
            "object": {
                "host_id": SYNTHETIC_HOST_ID,
                "id": meeting_id,
            }
        },
    }


def _clear_lock(redis, meeting_id: str) -> None:
    redis.delete(f"recall:meeting:{meeting_id}:starting_lock")


def _probe_dependencies(redis) -> None:
    _clear_lock(redis, SYNTHETIC_MEETING_ID)
    zoom_webhooks.handle_meeting_started(_payload(SYNTHETIC_MEETING_ID))


def signed_headers(body: bytes) -> dict[str, str]:
    timestamp = str(int(time.time()))
    signature = (
        "v0="
        + hmac.new(
            ZOOM_SECRET_TOKEN.encode(),
            b"v0:" + timestamp.encode() + b":" + body,
            hashlib.sha256,
        ).hexdigest()
    )
    return {
        "Content-Type": "application/json",
        "x-zm-request-timestamp": timestamp,
        "x-zm-signature": signature,
    }


def _probe_ingress(redis) -> None:
    """Local signing proves route acceptance, not the Marketplace secret."""
    _clear_lock(redis, SYNTHETIC_HTTP_MEETING_ID)

    body = json.dumps(_payload(SYNTHETIC_HTTP_MEETING_ID)).encode()
    url = f"{API_BASE_URL}/external/v1/webhooks/zoom"
    try:
        with httpx.Client(timeout=_PROBE_TIMEOUT) as client:
            response = client.post(url, content=body, headers=signed_headers(body))
    except httpx.HTTPError as exc:
        alerting.alert(
            alerting.WebhookRouteUnreachable,
            error_class=type(exc).__name__,
        )
        return

    if response.status_code != httpx.codes.OK:
        # A 401 means our locally signed request was rejected. It does not by
        # itself establish the Marketplace app's configured secret.
        alerting.alert(
            alerting.WebhookRouteUnreachable,
            status=response.status_code,
        )
        return

    try:
        response_body = response.json()
    except ValueError:
        response_body = None
    if not isinstance(response_body, dict):
        alerting.alert(
            alerting.WebhookRouteUnreachable,
            status=response.status_code,
            error_class="InvalidResponse",
        )
        return

    if response_body.get("duplicate"):
        log.warning(
            "zoom probes: ingress deduplicated, handler not enqueued",
            meeting_id=SYNTHETIC_HTTP_MEETING_ID,
        )
        return

    log.info("zoom probes: ingress ok")


def _probe_recall_credentials() -> None:
    try:
        recall.ping()
    except httpx.HTTPStatusError as exc:
        alerting.alert(
            alerting.RecallApiUnreachable,
            status=exc.response.status_code,
        )
        return
    except httpx.HTTPError as exc:
        alerting.alert(
            alerting.RecallApiUnreachable,
            error_class=type(exc).__name__,
        )
        return

    log.info("zoom probes: recall credentials ok")


def perform() -> None:
    redis = get_redis()

    _probe_dependencies(redis)

    if is_production() or is_staging():
        _probe_ingress(redis)
        _probe_recall_credentials()

    log.info("zoom probes completed")


perform_celery = celery_app.task(name="app.jobs.synthetic_zoom_check.perform")(perform)
