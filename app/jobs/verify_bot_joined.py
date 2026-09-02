"""Detect expected Zoom captures that never start.

The watchdog reads settled lifecycle state, stays quiet for expected waiting
states, and emits a PHI-scrubbed alert for a genuinely stuck visit.
"""

import secrets
from typing import cast

from app import log
from app.celery import celery_app
from app.configuration.redis import get_redis
from app.services import alerting

# Avoid mistaking a slow-but-working dispatch for failure.
FIRST_CHECK_DELAY_SECONDS = 180
RECHECK_INTERVAL_SECONDS = 180
# 1 + 4 rechecks = 15 minutes of bounded coverage.
MAX_CHECKS = 5

_ALERTED_TTL_SECONDS = 24 * 60 * 60
_QUEUED_TTL_SECONDS = 24 * 60 * 60
_QUEUE_CLAIM_TTL_SECONDS = 5 * 60
_FINALIZED_QUEUE_CLAIM_PREFIX = "queued:"
_ACQUIRE_OR_CONFIRM_QUEUE_CLAIM = """
local value = redis.call('GET', KEYS[1])
if not value then
  local claimed = redis.call('SET', KEYS[1], ARGV[1], 'NX', 'EX', ARGV[2])
  if claimed then return 1 end
  value = redis.call('GET', KEYS[1])
end
if value == ARGV[1] then
  return 1
end
if string.sub(value, 1, string.len(ARGV[3])) == ARGV[3] or
   redis.call('TTL', KEYS[1]) > tonumber(ARGV[2]) then return 0 end
return -1
"""
_RELEASE_OWNED_QUEUE_CLAIM = """
local value = redis.call('GET', KEYS[1])
if not value or value ~= ARGV[1] then
  return 0
end
return redis.call('DEL', KEYS[1])
"""
_FINALIZE_OWNED_QUEUE_CLAIM = """
local value = redis.call('GET', KEYS[1])
if value == ARGV[2] then
  return 1
end
if not value or value ~= ARGV[1] then
  return 0
end
redis.call('SET', KEYS[1], ARGV[2], 'EX', ARGV[3])
return 1
"""
# Do not occupy a worker behind a live lifecycle handler.
_LOCK_BLOCKING_TIMEOUT_SECONDS = 5
_LOCK_TIMEOUT_SECONDS = 30


class WatchdogQueueClaimPending(Exception):
    "A prior publisher still owns the short watchdog queue lease."


def _rget(redis, key: str) -> str | None:
    val = redis.get(key)
    return val.decode() if isinstance(val, bytes) else val


def _still_failing(redis, prefix: str, occurrence_digest: str | None) -> bool:
    """Re-read health gates under the lifecycle lock to reject torn reads."""
    if not _occurrence_matches(redis, prefix, occurrence_digest):
        return False
    if not redis.get(f"{prefix}:doctor_id"):
        return False
    if redis.get(f"{prefix}:recording_active"):
        return False
    if redis.get(f"{prefix}:capture_started"):
        return False
    if redis.get(f"{prefix}:paused_at") or redis.get(f"{prefix}:merge_task_id"):
        return False
    return cast(int, redis.scard(f"{prefix}:participants_present")) > 0


def _occurrence_matches(redis, prefix: str, occurrence_digest: str | None) -> bool:
    """Fence a delayed check from a later reuse of the same meeting ID."""
    if occurrence_digest is None:
        # Rolling compatibility for tasks armed before occurrence fencing.
        return True
    return _rget(redis, f"{prefix}:occurrence_digest") == occurrence_digest


def perform(
    meeting_id: str,
    check_number: int = 1,
    occurrence_digest: str | None = None,
) -> None:
    redis = get_redis()
    prefix = f"recall:meeting:{meeting_id}"

    if not _occurrence_matches(redis, prefix, occurrence_digest):
        log.info(
            "join watchdog: occurrence changed, standing down",
            meeting_ref=alerting.ref(meeting_id),
            check_number=check_number,
        )
        return

    # meeting.ended clears per-meeting state.
    if not redis.get(f"{prefix}:doctor_id"):
        log.info(
            "join watchdog: meeting state gone, nothing to check",
            meeting_ref=alerting.ref(meeting_id),
            check_number=check_number,
        )
        return

    if redis.get(f"{prefix}:recording_active"):
        log.info(
            "join watchdog: recording active",
            meeting_ref=alerting.ref(meeting_id),
            check_number=check_number,
        )
        return

    # This visit already produced a recording; later pause is not dispatch failure.
    if redis.get(f"{prefix}:capture_started"):
        log.info(
            "join watchdog: capture already started for this session",
            meeting_ref=alerting.ref(meeting_id),
            check_number=check_number,
        )
        return

    # merge_window owns a deliberately paused, still-connected bot.
    if redis.get(f"{prefix}:paused_at") or redis.get(f"{prefix}:merge_task_id"):
        log.info(
            "join watchdog: recording paused inside merge window",
            meeting_ref=alerting.ref(meeting_id),
            check_number=check_number,
        )
        return

    doctor_present = bool(redis.get(f"{prefix}:doctor_present"))
    patient_count = cast(int, redis.scard(f"{prefix}:participants_present"))
    bot_id = _rget(redis, f"{prefix}:bot_id")

    if patient_count <= 0:
        # An empty room or doctor alone is expected.
        _rearm(meeting_id, check_number, occurrence_digest)
        return

    if not doctor_present:
        alert_type = alerting.StuckHoldingForDoctor
    else:
        alert_type = alerting.NoRecordingWithBothPresent

    # Recheck under the handler's lock before paging on independently read keys.
    from app.services.zoom_webhooks import lifecycle_lock_key

    lock = redis.lock(
        lifecycle_lock_key(meeting_id),
        timeout=_LOCK_TIMEOUT_SECONDS,
        blocking_timeout=_LOCK_BLOCKING_TIMEOUT_SECONDS,
    )
    if not lock.acquire(blocking=True):
        # A handler is mid-transition; defer rather than guess.
        log.info(
            "join watchdog: lifecycle busy, deferring verdict",
            meeting_ref=alerting.ref(meeting_id),
            check_number=check_number,
        )
        _rearm(meeting_id, check_number, occurrence_digest)
        return

    try:
        if not _still_failing(redis, prefix, occurrence_digest):
            log.info(
                "join watchdog: state settled before alerting, no action",
                meeting_ref=alerting.ref(meeting_id),
                check_number=check_number,
            )
            return

        alerted_key = f"{prefix}:watchdog_alerted"
        if redis.get(alerted_key):
            log.info(
                "join watchdog: already alerted for this meeting",
                meeting_ref=alerting.ref(meeting_id),
                check_number=check_number,
            )
            return

        event_id = alerting.alert(
            alert_type,
            meeting_ref=alerting.ref(meeting_id),
            bot_ref=alerting.ref(bot_id),
            doctor_present=doctor_present,
            patient_count=patient_count,
            minutes_since_start=(
                FIRST_CHECK_DELAY_SECONDS
                + (check_number - 1) * RECHECK_INTERVAL_SECONDS
            )
            // 60,
        )
        if event_id is None:
            raise RuntimeError("watchdog alert delivery was not accepted")
        # Mark only after Sentry accepts the event. A crash can duplicate an
        # alert, but it cannot suppress the only attempt for 24 hours.
        redis.set(alerted_key, alert_type.__name__, ex=_ALERTED_TTL_SECONDS)
    finally:
        try:
            lock.release()
        except Exception:
            # Lock already expired via its own timeout; the verdict still holds.
            log.info("join watchdog: lifecycle lock already released")


def _delayed_execution_available() -> bool:
    """Avoid recursive inline checks when eager mode ignores countdowns."""
    return not celery_app.conf.task_always_eager


def _rearm(
    meeting_id: str, check_number: int, occurrence_digest: str | None = None
) -> None:
    if not _delayed_execution_available():
        return

    if check_number >= MAX_CHECKS:
        log.info(
            "join watchdog: check budget exhausted, standing down",
            meeting_ref=alerting.ref(meeting_id),
            check_number=check_number,
        )
        return

    perform_celery.apply_async(
        args=(meeting_id, check_number + 1, occurrence_digest),
        countdown=RECHECK_INTERVAL_SECONDS,
    )


perform_celery = celery_app.task(name="app.jobs.verify_bot_joined.perform")(perform)


def queue(
    meeting_id: str,
    countdown: int = FIRST_CHECK_DELAY_SECONDS,
    *,
    occurrence_digest: str | None = None,
):
    """Arm the join watchdog. Idempotent per meeting.

    Arming twice would double every check and race the single-alert guard.
    """
    if not _delayed_execution_available():
        log.info("join watchdog not armed: eager mode")
        return None

    redis = get_redis()
    queue_key = f"recall:meeting:{meeting_id}:watchdog_queued"
    claim = secrets.token_hex(16)
    claim_state = redis.eval(
        _ACQUIRE_OR_CONFIRM_QUEUE_CLAIM,
        1,
        queue_key,
        claim,
        str(_QUEUE_CLAIM_TTL_SECONDS),
        _FINALIZED_QUEUE_CLAIM_PREFIX,
    )
    if claim_state == 0:
        return None
    if claim_state != 1:
        raise WatchdogQueueClaimPending()

    try:
        result = perform_celery.apply_async(
            args=(meeting_id, 1, occurrence_digest), countdown=countdown
        )
    except Exception:
        try:
            released = bool(redis.eval(_RELEASE_OWNED_QUEUE_CLAIM, 1, queue_key, claim))
        except Exception:
            # Preserve the broker exception: the durable intent reports it,
            # while this provisional claim still expires on its short TTL.
            log.warning(
                "join watchdog: queue claim cleanup failed",
                meeting_ref=alerting.ref(meeting_id),
            )
        else:
            if not released:
                # The key expired or a later publisher now owns it. Never
                # delete a replacement claim while unwinding this failure.
                log.info(
                    "join watchdog: queue claim ownership changed",
                    meeting_ref=alerting.ref(meeting_id),
                )
        raise

    try:
        finalized_claim = f"{_FINALIZED_QUEUE_CLAIM_PREFIX}{claim}"
        finalized = bool(
            redis.eval(
                _FINALIZE_OWNED_QUEUE_CLAIM,
                1,
                queue_key,
                claim,
                finalized_claim,
                str(_QUEUED_TTL_SECONDS),
            )
        )
    except Exception:
        # Publication succeeded. A short lease is safer than risking a
        # duplicate-suppressing claim that survives for a day without proof.
        log.warning(
            "join watchdog: queue claim finalization failed",
            meeting_ref=alerting.ref(meeting_id),
        )
        raise
    else:
        if not finalized:
            log.warning(
                "join watchdog: queue claim changed after publication",
                meeting_ref=alerting.ref(meeting_id),
            )
            raise RuntimeError("watchdog queue claim ownership changed")
    return result


def queue_durably(meeting_id: str, occurrence_digest: str):
    """Persist an encrypted retry source before publishing its opaque ID."""
    from app.services import external_webhook_inbox

    stored = external_webhook_inbox.store_zoom_watchdog_intent(
        meeting_id=meeting_id,
        occurrence_digest=occurrence_digest,
    )
    external_webhook_inbox.publish_delivery(stored.delivery_id)
    return stored
