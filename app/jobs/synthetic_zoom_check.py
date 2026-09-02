"""Dependency, ingress, and Recall credential probes for Zoom capture.

The locally signed ingress check proves TLS/routing, local signer-verifier
consistency, and durable-inbox acceptance—not the Zoom Marketplace secret or
bot capture. Those require a provider callback and a synthetic meeting. The
sentinel handler probe touches Redis and the database without matching a real
doctor; network probes run only in staging/production.
"""

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

# Intentional sentinel values. Never use these as real Zoom IDs anywhere
# else — the synthetic check would interfere with a real session that
# happened to collide.
SYNTHETIC_HOST_ID = "synthetic-monitor-not-a-real-zoom-host"
SYNTHETIC_MEETING_ID = "synthetic-monitor-meeting-id"
# The route probe uses its own meeting id so it exercises the handler in
# full. Sharing one id would mean the second probe short-circuits on the
# starting_lock the first one just took, and we would only be testing the
# dedup guard.
SYNTHETIC_HTTP_MEETING_ID = "synthetic-monitor-http-meeting-id"

_PROBE_TIMEOUT = httpx.Timeout(15.0, connect=5.0)


def _payload(meeting_id: str) -> dict:
    return {
        "event": "meeting.started",
        # Unique per run so the webhook route's 48h idempotency guard sees
        # a fresh event instead of replying `duplicate: true` — the whole
        # probe would otherwise stop reaching Celery after the first run.
        "event_ts": int(time.time() * 1000),
        "payload": {
            "object": {
                "host_id": SYNTHETIC_HOST_ID,
                "id": meeting_id,
            }
        },
    }


def _clear_lock(redis, meeting_id: str) -> None:
    # Wipe the dedup lock from the previous run so this iteration is
    # observable. Real meeting.started uses SET NX to prevent duplicate
    # bot creation; if we left the lock in place across runs, every
    # synthetic check after the first would short-circuit and we'd
    # stop exercising the doctor-lookup + redis-write code paths we
    # actually care about.
    redis.delete(f"recall:meeting:{meeting_id}:starting_lock")


def _probe_dependencies(redis) -> None:
    _clear_lock(redis, SYNTHETIC_MEETING_ID)
    zoom_webhooks.handle_meeting_started(_payload(SYNTHETIC_MEETING_ID))


def signed_headers(body: bytes) -> dict[str, str]:
    """Sign a probe body using Zoom's documented signature format.

    Kept public and separate so a test can feed the result straight into
    the route's own ``_verify_zoom_signature``. Without that, the probe
    could drift into signing something the endpoint would reject and we
    would be monitoring nothing.
    """
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
    """POST a locally signed webhook to our own public endpoint.

    This proves only that this deployment can reach its route and that its
    local signer and verifier agree. A provider-originated callback is required
    to prove the Zoom Marketplace app is configured with the same secret.
    """
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

    if response.json().get("duplicate"):
        # Not fatal, but it means the event never reached Celery, so the
        # probe proved less than it looks like it did.
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
