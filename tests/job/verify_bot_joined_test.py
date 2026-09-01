"""The watchdog has to page for real failures and stay quiet otherwise.

The quiet cases matter as much as the loud ones: a doctor sitting alone in a
room waiting for a patient is the single most common state this task will ever
observe, and an alert there would train everyone to ignore the channel inside
a day.
"""

import pytest

from app.configuration.redis import get_redis
from app.jobs import verify_bot_joined
from app.services import alerting

MEETING_ID = "watchdog-test-meeting"
PREFIX = f"recall:meeting:{MEETING_ID}"

_SUFFIXES = (
    "doctor_id",
    "doctor_present",
    "recording_active",
    "capture_started",
    "participants_present",
    "paused_at",
    "merge_task_id",
    "bot_id",
    "watchdog_alerted",
    "watchdog_queued",
)


@pytest.fixture
def redis():
    r = get_redis()
    for s in _SUFFIXES:
        r.delete(f"{PREFIX}:{s}")
    yield r
    for s in _SUFFIXES:
        r.delete(f"{PREFIX}:{s}")


def _live_session(redis):
    "the minimum state handle_meeting_started writes before arming us"
    redis.set(f"{PREFIX}:doctor_id", "doc-1")


# --------- quiet cases ---------


def test_no_alert_when_meeting_state_is_gone(redis, captured_alerts):
    """meeting.ended wipes every key. A watchdog still in flight then has
    nothing to judge, and the visit may well have recorded fine."""
    verify_bot_joined.perform(MEETING_ID)

    assert captured_alerts == []


def test_no_alert_while_recording(redis, captured_alerts):
    _live_session(redis)
    redis.set(f"{PREFIX}:recording_active", "1")
    redis.set(f"{PREFIX}:doctor_present", "1")
    redis.sadd(f"{PREFIX}:participants_present", "patient-a")

    verify_bot_joined.perform(MEETING_ID)

    assert captured_alerts == []


def test_no_alert_once_capture_has_started(redis, captured_alerts):
    """`capture_started` is set when Recall confirms a recording and outlives
    merge-window cleanup on purpose. This visit produced a recording, so
    whatever state it is in now is not a failure to dispatch -- including the
    deliberate refusal to resume without an exact encounter binding."""
    _live_session(redis)
    redis.set(f"{PREFIX}:capture_started", "1")
    redis.set(f"{PREFIX}:doctor_present", "1")
    redis.sadd(f"{PREFIX}:participants_present", "patient-a")

    verify_bot_joined.perform(MEETING_ID)

    assert captured_alerts == []


def test_no_alert_when_paused_inside_merge_window(redis, captured_alerts):
    """_pause_and_arm_merge_window deliberately clears recording_active while
    keeping the bot connected. That is a healthy state, and it looks exactly
    like a failure if you only check recording_active."""
    _live_session(redis)
    redis.set(f"{PREFIX}:doctor_present", "1")
    redis.sadd(f"{PREFIX}:participants_present", "patient-a")
    redis.set(f"{PREFIX}:paused_at", "2026-09-01T12:00:00")

    verify_bot_joined.perform(MEETING_ID)

    assert captured_alerts == []


def test_no_alert_when_the_doctor_is_alone(redis, captured_alerts):
    """The normal case: doctor joins early, patient has not arrived. No
    recording is correct here, and it is the state the watchdog sees most."""
    _live_session(redis)
    redis.set(f"{PREFIX}:doctor_present", "1")

    verify_bot_joined.perform(MEETING_ID)

    assert captured_alerts == []


def test_no_alert_for_an_empty_room(redis, captured_alerts):
    _live_session(redis)

    verify_bot_joined.perform(MEETING_ID)

    assert captured_alerts == []


# --------- loud cases ---------


def test_alerts_when_patients_waited_but_no_one_was_the_doctor(redis, captured_alerts):
    """The "holding" dead end. handle_participant_joined logs "non-doctor
    joined before doctor, holding" at info and returns, and nothing else ever
    fires -- so this task is the only thing that can notice the lost visit."""
    _live_session(redis)
    redis.sadd(f"{PREFIX}:participants_present", "patient-a")

    verify_bot_joined.perform(MEETING_ID)

    assert len(captured_alerts) == 1
    assert captured_alerts[0]["type"] is alerting.StuckHoldingForDoctor


def test_alerts_when_doctor_and_patient_are_present_with_no_recording(
    redis, captured_alerts
):
    _live_session(redis)
    redis.set(f"{PREFIX}:doctor_present", "1")
    redis.sadd(f"{PREFIX}:participants_present", "patient-a")

    verify_bot_joined.perform(MEETING_ID)

    assert len(captured_alerts) == 1
    assert captured_alerts[0]["type"] is alerting.NoRecordingWithBothPresent
    assert captured_alerts[0]["patient_count"] == 1
    assert captured_alerts[0]["doctor_present"] is True


def test_alerts_at_most_once_per_meeting(redis, captured_alerts):
    """Re-checks run every few minutes while a bad state persists. Paging on
    each turns a single failed visit into a five-alert storm."""
    _live_session(redis)
    redis.set(f"{PREFIX}:doctor_present", "1")
    redis.sadd(f"{PREFIX}:participants_present", "patient-a")

    verify_bot_joined.perform(MEETING_ID, check_number=1)
    verify_bot_joined.perform(MEETING_ID, check_number=2)
    verify_bot_joined.perform(MEETING_ID, check_number=3)

    assert len(captured_alerts) == 1


def test_alert_reports_how_long_the_visit_had_been_broken(redis, captured_alerts):
    """A failure caught on the fourth check has been going twelve minutes, not
    three. The on-call needs to know whether the visit is still saveable."""
    _live_session(redis)
    redis.set(f"{PREFIX}:doctor_present", "1")
    redis.sadd(f"{PREFIX}:participants_present", "patient-a")

    verify_bot_joined.perform(MEETING_ID, check_number=4)

    assert captured_alerts[0]["minutes_since_start"] == 12


def test_occupancy_counts_people_not_webhooks(redis, captured_alerts):
    """Occupancy comes from the participants set the handlers maintain, so a
    redelivered join counts once."""
    _live_session(redis)
    redis.sadd(f"{PREFIX}:participants_present", "patient-a")
    redis.sadd(f"{PREFIX}:participants_present", "patient-a")
    redis.set(f"{PREFIX}:doctor_present", "1")

    verify_bot_joined.perform(MEETING_ID)

    assert captured_alerts[0]["patient_count"] == 1


def test_the_alert_carries_no_raw_identifiers(redis, captured_alerts):
    """Alert context leaves the process. Meeting and bot ids go through the
    same non-reversible reference the webhook adapters use."""
    _live_session(redis)
    redis.set(f"{PREFIX}:doctor_present", "1")
    redis.set(f"{PREFIX}:bot_id", "recall-bot-secret")
    redis.sadd(f"{PREFIX}:participants_present", "patient-a")

    verify_bot_joined.perform(MEETING_ID)

    rendered = repr(captured_alerts[0])
    assert MEETING_ID not in rendered
    assert "recall-bot-secret" not in rendered
    assert captured_alerts[0]["bot_ref"].startswith("hmac:")


# --------- wiring ---------


def test_task_is_registered_under_the_name_beat_and_revocation_use():
    from app.celery import celery_app

    assert "app.jobs.verify_bot_joined.perform" in celery_app.tasks


def test_meeting_started_arms_the_watchdog_through_this_module():
    """The watchdog is worthless if nothing arms it, and the arming lives in
    zoom_webhooks. Pin the wiring so a refactor there cannot quietly drop it."""
    from app.services import zoom_webhooks

    assert zoom_webhooks.verify_bot_joined is verify_bot_joined
