"""Celery task that finalizes a paused recording once the 10-min merge
window has elapsed without a resume.

Pausing happens in zoom_webhooks: when the doctor leaves (or the last
non-doctor leaves while the doctor stays) we call recall.stop_recording
but keep the bot connected and arm this task. If a valid participant
combination returns inside the window, the webhook revokes this task
and issues recall.start_recording on the same bot — two recording
segments tied to the same appointment that summarize.py later merges.

If the window expires with no resume, we leave_call to disconnect the
bot and clear per-meeting Redis state."""

from app import log
from app.celery import celery_app
from app.configuration.redis import get_redis
from app.services import recall
from app.services.external_webhook_inbox import provider_log_ref


def _rget(redis, key: str) -> str | None:
    val = redis.get(key)
    return val.decode() if isinstance(val, bytes) else val


def perform(meeting_id: str) -> None:
    redis = get_redis()
    key_prefix = f"recall:meeting:{meeting_id}"

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
    try:
        recall.leave_call(bot_id)
    except Exception as exc:
        log.warning(
            "leave_call failed during merge window expiry",
            bot_ref=provider_log_ref(bot_id),
            meeting_ref=provider_log_ref(meeting_id),
            error_type=type(exc).__name__,
        )

    for suffix in (
        "bot_id",
        "merge_task_id",
        "paused_at",
        "participants_order",
        "participants_names",
        "patient_count",
    ):
        redis.delete(f"{key_prefix}:{suffix}")


expire = celery_app.task(perform)


def queue(meeting_id: str, countdown: int = 600):
    """Schedule the merge-window finalize task. Returns the Celery
    AsyncResult so the caller can persist task_id for later revocation."""
    return expire.apply_async(args=(meeting_id,), countdown=countdown)
