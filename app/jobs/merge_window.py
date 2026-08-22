"""Celery task that releases a stopped bot after the 10-minute safety window.

Pausing happens in zoom_webhooks: when the doctor leaves (or the last
non-doctor leaves while the doctor stays) we call recall.stop_recording,
keep the bot handle, and arm this task. The legacy path cannot prove that a
returning participant belongs to the same encounter, so it never resumes the
bot or creates a second segment.

When the window expires, we leave_call to disconnect the
bot and clear the paused-bot state. The generation fence remains for its TTL
so a stale task cannot collide with a later use of the same Zoom meeting ID.
"""

from datetime import datetime, timedelta
from typing import Any

from app import log
from app.celery import celery_app
from app.configuration.redis import get_redis
from app.services import recall
from app.services.external_webhook_inbox import (
    complete_zoom_teardown_intent,
    process_delivery,
    provider_log_ref,
    store_zoom_teardown_intent,
)

from app.models.zoom_meeting import ZoomMeeting, ZoomMeetingStatus

_LIFECYCLE_LOCK_TIMEOUT_SECONDS = 180
_LIFECYCLE_LOCK_BLOCKING_TIMEOUT_SECONDS = 90


def _rget(redis, key: str) -> str | None:
    val = redis.get(key)
    return val.decode() if isinstance(val, bytes) else val


def _same_occurrence(current: str | None, expected: str | None) -> bool:
    # A legacy task may act only on legacy state. Once a UUID-bound occurrence
    # exists, every old two-argument task is fenced.
    return current == expected


def _clear_released_bot_state(redis, key_prefix: str, *, cleanup_session: bool) -> None:
    suffixes = [
        "bot_id",
        "merge_task_id",
        "paused_at",
        "participants_order",
        "participants_names",
        "patient_count",
        "participants_present",
        "participant_event_order",
        "merge_release_intent_id",
    ]
    if cleanup_session:
        suffixes.extend(
            [
                "doctor_id",
                "host_id",
                "doctor_present",
                "recording_active",
                "capture_started",
                "occurrence_digest",
                "starting_lock",
            ]
        )
    for suffix in suffixes:
        redis.delete(f"{key_prefix}:{suffix}")


def perform(
    meeting_id: str | None = None,
    generation: int | None = None,
    occurrence_digest: str | None = None,
    release_delivery_id: str | None = None,
) -> None:
    if release_delivery_id is not None:
        # The task carries only an internal TypeID. The encrypted inbox row is
        # the durable source of the bot handle and occurrence/generation fence,
        # so Redis loss cannot turn expiry into a silent no-op.
        process_delivery(release_delivery_id)
        return
    if meeting_id is None:
        raise ValueError("legacy merge-window task requires meeting_id")

    redis = get_redis()
    key_prefix = f"recall:meeting:{meeting_id}"

    # The same lock is held across Recall start/stop/leave transitions in the
    # Zoom handlers. Whichever operation wins becomes provider-visible first;
    # the loser then re-reads the authoritative local state instead of acting
    # on a pre-lock snapshot.
    with redis.lock(
        f"{key_prefix}:lifecycle_lock",
        timeout=_LIFECYCLE_LOCK_TIMEOUT_SECONDS,
        blocking_timeout=_LIFECYCLE_LOCK_BLOCKING_TIMEOUT_SECONDS,
    ):
        current_generation = _rget(redis, f"{key_prefix}:merge_generation")
        current_occurrence = _rget(redis, f"{key_prefix}:occurrence_digest")
        if not _same_occurrence(current_occurrence, occurrence_digest):
            log.info(
                "merge window: stale occurrence ignored",
                meeting_ref=provider_log_ref(meeting_id),
            )
            return
        if generation is None:
            # Rolling compatibility: a task queued by the old API has no
            # generation and may finalize only an equally old pause. Once a
            # new API has created a generation, every legacy task is stale.
            if current_generation is not None:
                log.info(
                    "merge window: legacy task fenced by current generation",
                    meeting_ref=provider_log_ref(meeting_id),
                )
                return
        elif current_generation != str(generation):
            log.info(
                "merge window: stale generation ignored",
                meeting_ref=provider_log_ref(meeting_id),
            )
            return

        if redis.get(f"{key_prefix}:recording_active"):
            log.info(
                "merge window: recording resumed before expiry, no action",
                meeting_ref=provider_log_ref(meeting_id),
            )
            return

        bot_id = _rget(redis, f"{key_prefix}:bot_id")
        if not bot_id:
            log.info(
                "merge window: no bot tracked, nothing to finalize",
                meeting_ref=provider_log_ref(meeting_id),
            )
            return

        log.info(
            "merge window expired, releasing bot",
            meeting_ref=provider_log_ref(meeting_id),
            bot_ref=provider_log_ref(bot_id),
        )
        intent = store_zoom_teardown_intent(
            action="release",
            meeting_id=meeting_id,
            bot_id=bot_id,
            occurrence_digest=occurrence_digest,
            generation=generation,
            stop_recording=False,
            cleanup_session=False,
        )
        try:
            recall.leave_call(bot_id)
        except Exception as exc:
            log.warning(
                "leave_call failed during merge window expiry; retaining bot state",
                bot_ref=provider_log_ref(bot_id),
                meeting_ref=provider_log_ref(meeting_id),
                error_type=type(exc).__name__,
            )
            # Keep bot_id and the pause/task metadata available for an
            # operator or a retry.  A raised Celery failure is observable;
            # silent cleanup would orphan a possibly connected recording bot.
            raise

        _clear_released_bot_state(redis, key_prefix, cleanup_session=False)
        complete_zoom_teardown_intent(intent.delivery_id)


expire = celery_app.task(perform)


def queue(
    meeting_id: str,
    generation: int,
    *,
    occurrence_digest: str | None = None,
    release_delivery_id: str | None = None,
    countdown: int = 600,
):
    """Schedule the merge-window finalize task. Returns the Celery
    AsyncResult so the caller can persist task_id for later revocation."""
    if release_delivery_id is not None:
        # New tasks expose only our opaque internal TypeID to the broker. Raw
        # Zoom/Recall identifiers remain encrypted in the durable inbox row.
        return expire.apply_async(
            kwargs={"release_delivery_id": release_delivery_id},
            countdown=countdown,
        )
    # Rolling compatibility for an in-flight old-style caller. New production
    # paths always supply a durable release delivery ID above.
    return expire.apply_async(
        args=(meeting_id, generation, occurrence_digest),
        countdown=countdown,
    )


def mark_meeting_ended(doctor_id: str | None, meeting_id: str) -> bool:
    """Idempotently repair the dashboard lifecycle after provider teardown."""

    if doctor_id is None:
        return False
    zoom_mtg = ZoomMeeting.get(
        doctor_id=doctor_id,
        zoom_meeting_id=meeting_id,
    )
    if zoom_mtg is None or zoom_mtg.status == ZoomMeetingStatus.ended:
        return zoom_mtg is not None
    zoom_mtg.status = ZoomMeetingStatus.ended
    zoom_mtg.save()
    return True


def reconcile_teardown_intent(event_type: str, payload: dict[str, Any]) -> None:
    """Retry one encrypted teardown intent discovered by the inbox."""

    intent = payload.get("teardown_intent")
    if not isinstance(intent, dict):
        raise ValueError("invalid Zoom teardown intent")
    meeting_id = intent.get("meeting_id")
    bot_id = intent.get("bot_id")
    doctor_id = intent.get("doctor_id")
    occurrence_digest = intent.get("occurrence_digest")
    generation_value = intent.get("generation")
    if not isinstance(meeting_id, str) or not isinstance(bot_id, str):
        raise ValueError("invalid Zoom teardown target")
    if doctor_id is not None and not isinstance(doctor_id, str):
        raise ValueError("invalid Zoom teardown doctor")
    if occurrence_digest is not None and not isinstance(occurrence_digest, str):
        raise ValueError("invalid Zoom occurrence fence")
    generation = int(generation_value) if generation_value is not None else None
    stop_recording = intent.get("stop_recording") is True
    cleanup_session = intent.get("cleanup_session") is True
    mark_ended = intent.get("mark_meeting_ended") is True

    redis = get_redis()
    key_prefix = f"recall:meeting:{meeting_id}"
    with redis.lock(
        f"{key_prefix}:lifecycle_lock",
        timeout=_LIFECYCLE_LOCK_TIMEOUT_SECONDS,
        blocking_timeout=_LIFECYCLE_LOCK_BLOCKING_TIMEOUT_SECONDS,
    ):
        current_bot = _rget(redis, f"{key_prefix}:bot_id")
        current_occurrence = _rget(redis, f"{key_prefix}:occurrence_digest")
        owns_current_state = current_bot == bot_id and _same_occurrence(
            current_occurrence, occurrence_digest
        )

        if event_type == "internal.zoom_bot_pause":
            if not owns_current_state:
                # Redis loss or a later lifecycle must not orphan the bot from
                # the durable intent. Disconnect it without touching newer
                # occurrence state.
                try:
                    recall.stop_recording(bot_id)
                finally:
                    recall.leave_call(bot_id)
                release_intent = store_zoom_teardown_intent(
                    action="release",
                    meeting_id=meeting_id,
                    bot_id=bot_id,
                    occurrence_digest=occurrence_digest,
                    generation=generation,
                    stop_recording=True,
                    cleanup_session=False,
                    recovery_delay=timedelta(minutes=10),
                )
                complete_zoom_teardown_intent(release_intent.delivery_id)
                return
            if generation is None:
                raise ValueError("pause intent requires generation")
            recall.stop_recording(bot_id)
            ttl = timedelta(hours=24)
            redis.set(f"{key_prefix}:merge_generation", generation, ex=ttl)
            redis.delete(f"{key_prefix}:recording_active")
            redis.set(
                f"{key_prefix}:paused_at",
                datetime.utcnow().isoformat(),
                ex=ttl,
            )
            release_intent = store_zoom_teardown_intent(
                action="release",
                meeting_id=meeting_id,
                bot_id=bot_id,
                occurrence_digest=occurrence_digest,
                generation=generation,
                stop_recording=True,
                cleanup_session=False,
                recovery_delay=timedelta(minutes=10),
            )
            task = queue(
                meeting_id,
                generation,
                occurrence_digest=occurrence_digest,
                release_delivery_id=release_intent.delivery_id,
                countdown=600,
            )
            redis.set(f"{key_prefix}:merge_task_id", task.id, ex=ttl)
            redis.set(
                f"{key_prefix}:merge_release_intent_id",
                release_intent.delivery_id,
                ex=ttl,
            )
            return

        if event_type != "internal.zoom_bot_release":
            raise ValueError("unsupported Zoom teardown intent")

        if stop_recording:
            try:
                recall.stop_recording(bot_id)
            except Exception as exc:
                log.warning(
                    "reconciled teardown stop failed; attempting leave",
                    meeting_ref=provider_log_ref(meeting_id),
                    bot_ref=provider_log_ref(bot_id),
                    error_type=type(exc).__name__,
                )
        recall.leave_call(bot_id)
        if mark_ended:
            mark_meeting_ended(doctor_id, meeting_id)
        if owns_current_state:
            _clear_released_bot_state(
                redis, key_prefix, cleanup_session=cleanup_session
            )
