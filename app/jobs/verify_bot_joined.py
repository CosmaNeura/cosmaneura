"""Detect expected Zoom captures that never start.

The watchdog reads settled lifecycle state, stays quiet for expected waiting
states, and emits a PHI-scrubbed alert for a genuinely stuck visit.
"""

from typing import cast

from app import log
from app.celery import celery_app
from app.configuration.redis import get_redis
from app.services import alerting
from app.services.external_webhook_inbox import ZOOM_WATCHDOG_MAX_CHECKS

# Avoid mistaking a slow-but-working dispatch for failure.
FIRST_CHECK_DELAY_SECONDS = 180
RECHECK_INTERVAL_SECONDS = 180
# 1 + 4 rechecks = 15 minutes of bounded coverage.
MAX_CHECKS = ZOOM_WATCHDOG_MAX_CHECKS

_ALERTED_TTL_SECONDS = 24 * 60 * 60
# Do not occupy a worker behind a live lifecycle handler.
_LOCK_BLOCKING_TIMEOUT_SECONDS = 5
_LOCK_TIMEOUT_SECONDS = 30


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


def _capture_still_expected(
    meeting_record_id: str, meeting_id: str, occurrence_digest: str
) -> bool:
    from app.models.zoom_meeting import ZoomMeeting, ZoomMeetingStatus

    meeting = cast(
        ZoomMeeting | None,
        ZoomMeeting.get(id=meeting_record_id),
    )
    return bool(
        meeting is not None
        and meeting.zoom_meeting_id == meeting_id
        and meeting.capture_authorization_occurrence_digest == occurrence_digest
        and meeting.status in {ZoomMeetingStatus.scheduled, ZoomMeetingStatus.started}
    )


def _alert_once(
    redis,
    prefix: str,
    occurrence_digest: str | None,
    alert_type: type[alerting.DispatchAlert],
    **context: object,
) -> None:
    alerted_key = f"{prefix}:watchdog_alerted:{occurrence_digest or 'legacy'}"
    if redis.get(alerted_key):
        log.info("join watchdog: already alerted", meeting_ref=context["meeting_ref"])
        return
    event_id = alerting.alert(alert_type, **context)
    if event_id is None:
        raise RuntimeError("Sentry SDK did not accept the watchdog event")
    # The event ID proves local SDK acceptance, not server receipt or human
    # notification; the flight plan verifies those separately in staging.
    redis.set(alerted_key, alert_type.__name__, ex=_ALERTED_TTL_SECONDS)


def _missing_state_needs_recheck(
    redis,
    meeting_record_id: str | None,
    meeting_id: str,
    occurrence_digest: str,
    check_number: int,
) -> bool:
    from app.services.zoom_webhooks import lifecycle_lock_key

    lock = redis.lock(
        lifecycle_lock_key(meeting_id),
        timeout=_LOCK_TIMEOUT_SECONDS,
        blocking_timeout=_LOCK_BLOCKING_TIMEOUT_SECONDS,
    )
    if not lock.acquire(blocking=True):
        if check_number >= MAX_CHECKS:
            raise RuntimeError("join watchdog lifecycle remained busy")
        return True
    try:
        prefix = f"recall:meeting:{meeting_id}"
        active = _rget(redis, f"{prefix}:occurrence_digest")
        if active == occurrence_digest and redis.get(f"{prefix}:doctor_id"):
            return True
        if active is not None and active != occurrence_digest:
            return False
        if meeting_record_id is not None and _capture_still_expected(
            meeting_record_id, meeting_id, occurrence_digest
        ):
            _alert_once(
                redis,
                prefix,
                occurrence_digest,
                alerting.LostSessionState,
                meeting_ref=alerting.ref(meeting_id),
                check_number=check_number,
            )
        return False
    finally:
        try:
            lock.release()
        except Exception:
            log.info("join watchdog: lifecycle lock already released")


def perform(
    meeting_id: str,
    check_number: int = 1,
    occurrence_digest: str | None = None,
    meeting_record_id: str | None = None,
) -> None:
    redis = get_redis()
    prefix = f"recall:meeting:{meeting_id}"

    if meeting_record_id is not None and (
        occurrence_digest is None
        or not _capture_still_expected(meeting_record_id, meeting_id, occurrence_digest)
    ):
        log.info(
            "join watchdog: durable target inactive",
            meeting_ref=alerting.ref(meeting_id),
        )
        return

    active_occurrence = _rget(redis, f"{prefix}:occurrence_digest")
    if occurrence_digest is not None and (
        active_occurrence is None
        or (
            active_occurrence == occurrence_digest
            and not redis.get(f"{prefix}:doctor_id")
        )
    ):
        needs_recheck = _missing_state_needs_recheck(
            redis,
            meeting_record_id,
            meeting_id,
            occurrence_digest,
            check_number,
        )
        if needs_recheck and check_number < MAX_CHECKS:
            _rearm(meeting_record_id, meeting_id, check_number, occurrence_digest)
            return
        if not needs_recheck:
            return
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

    patient_count = cast(int, redis.scard(f"{prefix}:participants_present"))

    if patient_count <= 0:
        # An empty room or doctor alone is expected.
        _rearm(meeting_record_id, meeting_id, check_number, occurrence_digest)
        return

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
        if check_number >= MAX_CHECKS:
            raise RuntimeError("join watchdog lifecycle remained busy")
        _rearm(meeting_record_id, meeting_id, check_number, occurrence_digest)
        return

    try:
        if not _still_failing(redis, prefix, occurrence_digest):
            log.info(
                "join watchdog: state settled before alerting, no action",
                meeting_ref=alerting.ref(meeting_id),
                check_number=check_number,
            )
            return

        doctor_present = bool(redis.get(f"{prefix}:doctor_present"))
        patient_count = cast(int, redis.scard(f"{prefix}:participants_present"))
        bot_id = _rget(redis, f"{prefix}:bot_id")
        alert_type = (
            alerting.NoRecordingWithBothPresent
            if doctor_present
            else alerting.StuckHoldingForDoctor
        )
        _alert_once(
            redis,
            prefix,
            occurrence_digest,
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
    meeting_record_id: str | None,
    meeting_id: str,
    check_number: int,
    occurrence_digest: str | None = None,
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

    next_check = check_number + 1
    if meeting_record_id is None or occurrence_digest is None:
        return
    queue_durably(
        meeting_record_id,
        meeting_id,
        occurrence_digest,
        check_number=next_check,
    )


def queue_durably(
    meeting_record_id: str,
    meeting_id: str,
    occurrence_digest: str,
    *,
    check_number: int = 1,
):
    """Schedule one check through its encrypted durable execution record."""
    from app.services import external_webhook_inbox

    if not _delayed_execution_available():
        log.info("join watchdog not armed: eager mode")
        return None
    countdown = (
        FIRST_CHECK_DELAY_SECONDS if check_number == 1 else RECHECK_INTERVAL_SECONDS
    )
    stored = external_webhook_inbox.store_zoom_watchdog_intent(
        meeting_record_id=meeting_record_id,
        meeting_id=meeting_id,
        occurrence_digest=occurrence_digest,
        check_number=check_number,
        delay_seconds=countdown,
    )
    try:
        external_webhook_inbox.publish_delivery(stored.delivery_id, countdown=countdown)
    except Exception as exc:
        alerting.alert(
            alerting.WatchdogArmingFailed,
            meeting_ref=alerting.ref(meeting_id),
            error_type=type(exc).__name__,
        )
    return stored
