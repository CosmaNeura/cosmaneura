"""Synthetic monitor that exercises the meeting.started webhook handler
against production redis + database every 15 minutes.

Where the heartbeat task (app.jobs.heartbeat) only proves the worker
is alive, this proves the actual code path that handles real user
traffic is still intact. It catches:

- env var drift (DATABASE_URL, REDIS_URL, ZOOM_* secrets unset/rotated)
- DB connectivity from the worker container
- Redis connectivity from the worker container
- Import-chain breakage from a bad deploy that boots but explodes at
  first real request
- Azure-side network policy changes that block prod egress

The payload uses host_ids that will never match a real
DoctorVideoIntegration row, so handle_meeting_started executes the
"no doctor found, skipping" branch instead of stamping live state into
redis. That branch still touches redis (lock acquire) and the DB
(doctor lookup), which is enough to validate the dependency chain.

Sentry CeleryIntegration with monitor_beat_tasks=True turns this into
a cron monitor: if the task fails or doesn't fire on schedule, the
on-call gets paged."""

from app import log
from app.celery import celery_app
from app.configuration.redis import get_redis
from app.services import zoom_webhooks

# Intentional sentinel values. Never use these as real Zoom IDs anywhere
# else — the synthetic check would interfere with a real session that
# happened to collide.
SYNTHETIC_HOST_ID = "synthetic-monitor-not-a-real-zoom-host"
SYNTHETIC_MEETING_ID = "synthetic-monitor-meeting-id"


def perform() -> None:
    redis = get_redis()
    # Wipe the dedup lock from the previous run so this iteration is
    # observable. Real meeting.started uses SET NX to prevent duplicate
    # bot creation; if we left the lock in place across runs, every
    # synthetic check after the first would short-circuit and we'd
    # stop exercising the doctor-lookup + redis-write code paths we
    # actually care about.
    redis.delete(f"recall:meeting:{SYNTHETIC_MEETING_ID}:starting_lock")

    payload = {
        "payload": {
            "object": {
                "host_id": SYNTHETIC_HOST_ID,
                "id": SYNTHETIC_MEETING_ID,
            }
        }
    }
    zoom_webhooks.handle_meeting_started(payload)

    log.info("synthetic zoom check completed")


perform_celery = celery_app.task(name="app.jobs.synthetic_zoom_check.perform")(perform)
