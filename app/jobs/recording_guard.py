"""Celery task that stops a Recall recording if the doctor has not
returned to the Zoom meeting within the grace period.

The task is safe to run even if the doctor has returned — its first
check is against Redis `doctor_present`, which is source of truth."""

from app import log
from app.celery import celery_app
from app.configuration.redis import get_redis
from app.services import recall


def _rget(redis, key: str) -> str | None:
    val = redis.get(key)
    return val.decode() if isinstance(val, bytes) else val


def perform(meeting_id: str) -> None:
    redis = get_redis()
    key_prefix = f"recall:meeting:{meeting_id}"

    if redis.get(f"{key_prefix}:doctor_present"):
        log.info(
            "grace check: doctor back in call, no action",
            meeting_id=meeting_id,
        )
        return

    bot_id = _rget(redis, f"{key_prefix}:bot_id")
    if not bot_id:
        log.info(
            "grace check: no bot tracked, nothing to stop",
            meeting_id=meeting_id,
        )
        return

    if not redis.get(f"{key_prefix}:recording_active"):
        log.info(
            "grace check: recording not active, nothing to stop",
            meeting_id=meeting_id,
            bot_id=bot_id,
        )
        return

    log.info(
        "grace period expired, stopping recording",
        meeting_id=meeting_id,
        bot_id=bot_id,
    )
    recall.stop_recording(bot_id)
    recall.leave_call(bot_id)
    redis.delete(f"{key_prefix}:recording_active")
    redis.delete(f"{key_prefix}:bot_id")
    redis.delete(f"{key_prefix}:grace_task_id")
    redis.delete(f"{key_prefix}:participants_order")
    redis.delete(f"{key_prefix}:participants_names")


stop_recording_if_absent = celery_app.task(perform)
