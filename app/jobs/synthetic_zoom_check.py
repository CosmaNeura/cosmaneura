"""Ingress and credential probes for the Zoom capture path.

What this does and does not establish, stated plainly because the previous
framing overclaimed: these probes prove that some pieces a capture *depends on*
are reachable, that the local webhook signer and verifier agree, and that the
Recall credential works. They do not prove the Zoom Marketplace signing secret
matches, or that a bot can join a meeting and produce a recording. Only a
provider-originated callback proves the former; a real bot joining a synthetic
meeting proves the latter. Both checks remain separate release evidence.

Where the heartbeat task (app.jobs.heartbeat) only proves the worker is alive,
these run every 15 minutes against production redis + database.

It runs three probes, each of which fails independently so one broken
dependency cannot mask another:

1. **Dependency probe** (in-process). Calls handle_meeting_started directly.
   Catches required env vars being unset, DB and Redis connectivity from the
   worker container,
   import-chain breakage from a deploy that boots but explodes on first
   real request, and Azure network policy changes blocking egress.

2. **Ingress probe** (HTTP). POSTs a locally signed synthetic webhook to our
   own public endpoint. This is the layer probe 1 cannot see: TLS and ingress,
   the FastAPI route, local signer/verifier consistency, and durable-inbox
   acceptance. Because both sides use this deployment's ``ZOOM_SECRET_TOKEN``,
   success cannot establish what secret Zoom Marketplace actually uses or
   prove that best-effort broker publication succeeded.

3. **Credential probe** (HTTP). One authenticated call to the Recall API.
   An invalid ``RECALL_API_KEY`` is otherwise discovered by the first
   patient visit that fails to record.

Probes 2 and 3 leave the container, so they only run in production and
staging; the handler probe runs everywhere.

The payloads use host_ids that will never match a real
DoctorVideoIntegration row, so handle_meeting_started executes the
"no doctor found, skipping" branch instead of stamping live state into
redis. That branch still touches redis (lock acquire) and the DB
(doctor lookup), which is enough to validate the dependency chain.

Sentry CeleryIntegration with monitor_beat_tasks=True turns this into a cron
monitor: if the task fails or doesn't fire on schedule, the on-call gets paged.
Probe failures alert through services.alerting instead, since a failed probe is
a finding, not a crash.

Not covered here, on purpose: an end-to-end bot-create/join/record smoke
against a dedicated non-PHI meeting. That is the only check that would prove
the note-taker actually works, and it costs a Recall bot per run, so its
cadence and its meeting are a product decision rather than something to bury
in this task.
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
