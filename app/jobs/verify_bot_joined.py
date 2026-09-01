"""Watchdog that notices a Zoom visit which never got its note-taker.

The dispatch path's abort branches log and return; none of them raises, and
``app.log`` does not reach Sentry (see ``services.alerting``). But the failures
that hurt most have *no* abort at all -- the session simply parks in a state it
never leaves. ``handle_participant_joined`` logging "non-doctor joined before
doctor, holding" at info and returning is the clearest example: that is the
last thing that ever happens, and there is no exception, no error, and no bot.

This task is the outside observer. It is armed for any meeting we are
supposed to cover, and a few minutes later it looks at the same Redis state the
webhook handlers write and asks one question: given who is in this room, should
a recording be running? If yes and none is, that is a failed visit and it pages.

Deliberately conservative about what counts as failure -- a doctor sitting
alone waiting for a patient is the most common state it will ever see, and
paging there would train everyone to ignore the channel inside a day. It
re-checks a bounded number of times and alerts at most once per meeting.
"""

from typing import cast

from app import log
from app.celery import celery_app
from app.configuration.redis import get_redis
from app.services import alerting

# First look happens well after the create_bot + wait-for-ready sequence, so a
# slow-but-working dispatch is never mistaken for a failure.
FIRST_CHECK_DELAY_SECONDS = 180
RECHECK_INTERVAL_SECONDS = 180
# 1 + 4 rechecks = 15 minutes of coverage. Past that a meeting with no
# recording is either over or a no-show, and re-arming forever would keep a
# task queued for every meeting of the day.
MAX_CHECKS = 5

_ALERTED_TTL_SECONDS = 24 * 60 * 60


def _rget(redis, key: str) -> str | None:
    val = redis.get(key)
    return val.decode() if isinstance(val, bytes) else val


def perform(meeting_id: str, check_number: int = 1) -> None:
    redis = get_redis()
    prefix = f"recall:meeting:{meeting_id}"

    # meeting.ended clears every per-meeting key. No state means the visit is
    # over and this check has nothing left to judge.
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

    # `capture_started` is set once Recall confirms a recording and outlives
    # merge-window cleanup on purpose. This visit produced a recording, so
    # whatever state it is in now is not a failure to dispatch -- including
    # the deliberate refusal to resume without an exact encounter binding.
    if redis.get(f"{prefix}:capture_started"):
        log.info(
            "join watchdog: capture already started for this session",
            meeting_ref=alerting.ref(meeting_id),
            check_number=check_number,
        )
        return

    # A paused recording inside the merge window is a deliberate state, not a
    # failure: the bot is still connected and merge_window owns its fate.
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
        # Doctor alone in the room, or an empty room. Expected -- dispatch is
        # supposed to wait for someone to record.
        _rearm(meeting_id, check_number)
        return

    if not doctor_present:
        # Patients are in the room but no participant was ever classified as
        # the host. This is the "holding" dead end: nothing else will fire.
        alert_type = alerting.StuckHoldingForDoctor
    else:
        # Doctor and patient both present with no recording: dispatch ran and
        # produced nothing, or never ran at all.
        alert_type = alerting.NoRecordingWithBothPresent

    # SET NX so a re-check (or a concurrent worker) pages once per meeting
    # rather than once every RECHECK_INTERVAL_SECONDS.
    if not redis.set(
        f"{prefix}:watchdog_alerted",
        alert_type.__name__,
        nx=True,
        ex=_ALERTED_TTL_SECONDS,
    ):
        log.info(
            "join watchdog: already alerted for this meeting",
            meeting_ref=alerting.ref(meeting_id),
            check_number=check_number,
        )
        return

    alerting.alert(
        alert_type,
        meeting_ref=alerting.ref(meeting_id),
        bot_ref=alerting.ref(bot_id),
        doctor_present=doctor_present,
        patient_count=patient_count,
        minutes_since_start=(
            FIRST_CHECK_DELAY_SECONDS + (check_number - 1) * RECHECK_INTERVAL_SECONDS
        )
        // 60,
    )


def _delayed_execution_available() -> bool:
    """False under ``task_always_eager``, where a countdown is ignored and
    ``apply_async`` runs the task inline.

    A watchdog exists to observe state *later*; run inline it would judge a
    session in the same instant the handler that armed it created it, and each
    re-arm would recurse through the whole check budget inside that handler.
    Tests exercise ``perform()`` directly against seeded Redis state instead,
    which is what actually needs covering.
    """
    return not celery_app.conf.task_always_eager


def _rearm(meeting_id: str, check_number: int) -> None:
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
        args=(meeting_id, check_number + 1),
        countdown=RECHECK_INTERVAL_SECONDS,
    )


perform_celery = celery_app.task(name="app.jobs.verify_bot_joined.perform")(perform)


def queue(meeting_id: str, countdown: int = FIRST_CHECK_DELAY_SECONDS):
    """Arm the join watchdog. Idempotent per meeting.

    Arming twice would double every check and race the single-alert guard.
    """
    if not _delayed_execution_available():
        log.info("join watchdog not armed: eager mode")
        return None

    redis = get_redis()
    if not redis.set(
        f"recall:meeting:{meeting_id}:watchdog_queued", "1", nx=True, ex=86400
    ):
        return None

    return perform_celery.apply_async(args=(meeting_id, 1), countdown=countdown)
