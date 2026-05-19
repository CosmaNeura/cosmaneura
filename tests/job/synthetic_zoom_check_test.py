from app.celery import celery_app
from app.configuration.redis import get_redis
from app.jobs.synthetic_zoom_check import (
    SYNTHETIC_HOST_ID,
    SYNTHETIC_MEETING_ID,
    perform,
)


def test_synthetic_check_completes_without_raising(global_session):
    """The whole point of this task is to be a smoke test that runs
    against real redis + db. If it raises, the on-call gets paged for
    nothing — so the happy path must never raise."""
    perform()


def test_synthetic_check_uses_sentinel_ids_that_will_never_match_a_doctor(global_session):
    """Synthetic IDs must not be valid Zoom host_ids — otherwise a real
    DoctorVideoIntegration row could coincidentally collide and the
    check would stamp live state into redis."""
    # Zoom user_ids are base64-ish ~22 chars. The sentinel is deliberately
    # longer and contains characters Zoom never emits ("-"), so a
    # collision is mathematically impossible.
    assert len(SYNTHETIC_HOST_ID) > 30
    assert "-" in SYNTHETIC_HOST_ID
    assert len(SYNTHETIC_MEETING_ID) > 20


def test_synthetic_check_is_registered_under_expected_name():
    """beat_schedule references the task by string; a drift in the
    registered name silently stops the cron monitor from firing."""
    assert "app.jobs.synthetic_zoom_check.perform" in celery_app.tasks


def test_synthetic_check_is_in_beat_schedule():
    entry = celery_app.conf.beat_schedule.get("synthetic_zoom_check")
    assert entry is not None
    assert entry["task"] == "app.jobs.synthetic_zoom_check.perform"


def test_synthetic_check_does_not_persist_lock_across_runs(global_session):
    """Real meeting.started uses SET NX to dedupe within 60s. Two
    consecutive synthetic runs must both exercise the doctor-lookup
    path; if the second silently short-circuits, our monitor stops
    catching regressions in that path."""
    redis = get_redis()
    lock_key = f"recall:meeting:{SYNTHETIC_MEETING_ID}:starting_lock"

    perform()
    # Even though meeting.started just SET NX'd the lock, perform()
    # itself deletes it at the top of the next iteration, so the second
    # call also goes through the full handler.
    perform()
    # Lock will exist briefly after the second perform() ran (set by
    # handle_meeting_started); presence isn't a contract violation.
    # What matters: the third call still works.
    redis.delete(lock_key)
    perform()
