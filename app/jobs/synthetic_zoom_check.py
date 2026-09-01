"""Synthetic monitor for the Zoom -> Recall note-taker pipeline.

Where the heartbeat task (app.jobs.heartbeat) only proves the worker is
alive, this proves the code path that handles real user traffic is still
intact, every 15 minutes, against production redis + database.

It runs three probes, each of which fails independently so one broken
dependency cannot mask another:

1. **Handler probe** (in-process). Calls handle_meeting_started directly.
   Catches env var drift (DATABASE_URL, REDIS_URL, ZOOM_* secrets unset
   or rotated), DB and Redis connectivity from the worker container,
   import-chain breakage from a deploy that boots but explodes on first
   real request, and Azure network policy changes blocking egress.

2. **Route probe** (HTTP). POSTs a correctly signed synthetic webhook to
   our own public endpoint. This is the layer probe 1 cannot see: TLS and
   ingress, the FastAPI route, ``ZOOM_SECRET_TOKEN`` actually matching
   what signs the request, and the Celery enqueue. It matters because a
   rotated secret rejects *every* Zoom webhook with a 401 and Zoom
   deactivates an endpoint after repeated failures -- a config change
   nobody deployed silently takes the note-taker offline for all doctors.

3. **Recall probe** (HTTP). One authenticated call to the Recall API.
   An invalid ``RECALL_API_KEY`` is otherwise discovered by the first
   patient visit that fails to record.

Probes 2 and 3 leave the container, so they only run in production and
staging; the handler probe runs everywhere.

The payloads use host_ids that will never match a real
DoctorVideoIntegration row, so handle_meeting_started executes the
"no doctor found, skipping" branch instead of stamping live state into
redis. That branch still touches redis (lock acquire) and the DB
(doctor lookup), which is enough to validate the dependency chain.

Sentry CeleryIntegration with monitor_beat_tasks=True turns this into a
cron monitor: if the task fails or doesn't fire on schedule, the on-call
gets paged. Probe failures alert through services.alerting instead, since
a failed probe is a finding, not a crash.
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


def _probe_handler(redis) -> None:
    _clear_lock(redis, SYNTHETIC_MEETING_ID)
    zoom_webhooks.handle_meeting_started(_payload(SYNTHETIC_MEETING_ID))


def signed_headers(body: bytes) -> dict[str, str]:
    """Sign a probe body exactly the way Zoom signs a real webhook.

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


def _probe_webhook_route(redis) -> None:
    """POST a correctly signed webhook to our own public endpoint.

    Signed with the same secret the route verifies against, so a mismatch
    here is exactly the mismatch that would reject real Zoom traffic.
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
        # A 401 here means ZOOM_SECRET_TOKEN no longer matches the Zoom app
        # and every real webhook is being rejected too.
        alerting.alert(
            alerting.WebhookRouteUnreachable,
            status=response.status_code,
        )
        return

    if response.json().get("duplicate"):
        # Not fatal, but it means the event never reached Celery, so the
        # probe proved less than it looks like it did.
        log.warning(
            "synthetic zoom check: webhook deduplicated, handler not enqueued",
            meeting_id=SYNTHETIC_HTTP_MEETING_ID,
        )
        return

    log.info("synthetic zoom check: webhook route ok")


def _probe_recall_api() -> None:
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

    log.info("synthetic zoom check: recall api ok")


def perform() -> None:
    redis = get_redis()

    _probe_handler(redis)

    if is_production() or is_staging():
        _probe_webhook_route(redis)
        _probe_recall_api()

    log.info("synthetic zoom check completed")


perform_celery = celery_app.task(name="app.jobs.synthetic_zoom_check.perform")(perform)
