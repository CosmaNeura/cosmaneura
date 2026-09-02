"""Join-watchdog safety and failure tests."""

import pytest
from typeid import TypeID

from app.configuration.redis import get_redis
from app.jobs import verify_bot_joined
from app.services import alerting, external_webhook_inbox

MEETING_ID = "watchdog-test-meeting"
MEETING_RECORD_ID = str(TypeID("zmtg"))
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
    "occurrence_digest",
    "watchdog_alerted",
)


def _alert_key(occurrence: str = "legacy") -> str:
    return f"{PREFIX}:watchdog_alerted:{occurrence}"


@pytest.fixture
def redis():
    r = get_redis()
    for s in _SUFFIXES:
        r.delete(f"{PREFIX}:{s}")
    for key in r.scan_iter(f"{PREFIX}:watchdog_alerted:*"):
        r.delete(key)
    yield r
    for s in _SUFFIXES:
        r.delete(f"{PREFIX}:{s}")
    for key in r.scan_iter(f"{PREFIX}:watchdog_alerted:*"):
        r.delete(key)


def _live_session(redis):
    "the minimum state handle_meeting_started writes before arming us"
    redis.set(f"{PREFIX}:doctor_id", "doc-1")


def test_no_alert_when_meeting_state_is_gone(redis, captured_alerts):
    verify_bot_joined.perform(MEETING_ID)

    assert captured_alerts == []


@pytest.mark.parametrize(
    "healthy_key", ["recording_active", "capture_started", "paused_at"]
)
def test_no_alert_for_nonfailure_session_state(redis, captured_alerts, healthy_key):
    _live_session(redis)
    redis.set(f"{PREFIX}:doctor_present", "1")
    redis.sadd(f"{PREFIX}:participants_present", "patient-a")
    redis.set(f"{PREFIX}:{healthy_key}", "1")

    verify_bot_joined.perform(MEETING_ID)

    assert captured_alerts == []


def test_no_alert_when_the_doctor_is_alone(redis, captured_alerts):
    _live_session(redis)
    redis.set(f"{PREFIX}:doctor_present", "1")

    verify_bot_joined.perform(MEETING_ID)

    assert captured_alerts == []


def test_alerts_when_patients_waited_but_no_one_was_the_doctor(redis, captured_alerts):
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
    redis.sadd(f"{PREFIX}:participants_present", "patient-a")

    verify_bot_joined.perform(MEETING_ID)

    assert len(captured_alerts) == 1
    assert captured_alerts[0]["type"] is alerting.NoRecordingWithBothPresent
    assert captured_alerts[0]["patient_count"] == 1
    assert captured_alerts[0]["doctor_present"] is True


def test_alerts_at_most_once_per_occurrence(redis, captured_alerts):
    _live_session(redis)
    redis.set(f"{PREFIX}:occurrence_digest", "occurrence-a")
    redis.set(f"{PREFIX}:doctor_present", "1")
    redis.sadd(f"{PREFIX}:participants_present", "patient-a")

    verify_bot_joined.perform(MEETING_ID, 1, "occurrence-a")
    verify_bot_joined.perform(MEETING_ID, 2, "occurrence-a")
    redis.set(f"{PREFIX}:occurrence_digest", "occurrence-b")
    verify_bot_joined.perform(MEETING_ID, 1, "occurrence-b")

    assert len(captured_alerts) == 2


@pytest.mark.parametrize("failure", [None, RuntimeError("sentry unavailable")])
def test_alert_failure_leaves_guard_open_for_retry(redis, monkeypatch, failure):
    _live_session(redis)
    redis.set(f"{PREFIX}:doctor_present", "1")
    redis.sadd(f"{PREFIX}:participants_present", "patient-a")
    outcomes = iter((failure, "event-id"))
    attempts: list[bool] = []

    def deliver(*_args, **_kwargs):
        attempts.append(True)
        outcome = next(outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(verify_bot_joined.alerting, "alert", deliver)
    with pytest.raises(RuntimeError):
        verify_bot_joined.perform(MEETING_ID)
    assert redis.get(_alert_key()) is None

    verify_bot_joined.perform(MEETING_ID)
    assert attempts == [True, True]
    assert redis.get(_alert_key()) is not None


def test_marker_write_failure_happens_after_alert_delivery(redis, monkeypatch):
    _live_session(redis)
    redis.sadd(f"{PREFIX}:participants_present", "patient-a")
    delivered: list[bool] = []
    monkeypatch.setattr(
        verify_bot_joined.alerting,
        "alert",
        lambda *_args, **_kwargs: delivered.append(True) or "event-id",
    )
    real_set = redis.set

    def fail_marker(key, *args, **kwargs):
        if key == _alert_key():
            raise ConnectionError()
        return real_set(key, *args, **kwargs)

    monkeypatch.setattr(redis, "set", fail_marker)
    with pytest.raises(ConnectionError):
        verify_bot_joined.perform(MEETING_ID)
    assert delivered == [True]


def test_alert_reports_how_long_the_visit_had_been_broken(redis, captured_alerts):
    _live_session(redis)
    redis.set(f"{PREFIX}:doctor_present", "1")
    redis.sadd(f"{PREFIX}:participants_present", "patient-a")

    verify_bot_joined.perform(MEETING_ID, check_number=4)

    assert captured_alerts[0]["minutes_since_start"] == 12


def test_the_alert_carries_no_raw_identifiers(redis, captured_alerts):
    _live_session(redis)
    redis.set(f"{PREFIX}:doctor_present", "1")
    redis.set(f"{PREFIX}:bot_id", "recall-bot-secret")
    redis.sadd(f"{PREFIX}:participants_present", "patient-a")

    verify_bot_joined.perform(MEETING_ID)

    rendered = repr(captured_alerts[0])
    assert MEETING_ID not in rendered
    assert "recall-bot-secret" not in rendered
    assert captured_alerts[0]["bot_ref"].startswith("hmac:")


def test_a_check_from_a_previous_occurrence_stands_down(redis, captured_alerts):
    _live_session(redis)
    redis.set(f"{PREFIX}:occurrence_digest", "occurrence-b")
    redis.sadd(f"{PREFIX}:participants_present", "patient-a")

    verify_bot_joined.perform(MEETING_ID, occurrence_digest="occurrence-a")

    assert captured_alerts == []


def test_missing_active_state_alerts_when_capture_is_still_expected(
    redis, captured_alerts, monkeypatch
):
    monkeypatch.setattr(verify_bot_joined, "_capture_still_expected", lambda *_: True)

    verify_bot_joined.perform(
        MEETING_ID,
        occurrence_digest="occurrence-a",
        meeting_record_id=MEETING_RECORD_ID,
    )

    assert [call["type"] for call in captured_alerts] == [alerting.LostSessionState]
    assert redis.get(_alert_key("occurrence-a")) is not None


def test_missing_active_state_stands_down_after_a_proven_end(
    redis, captured_alerts, monkeypatch
):
    monkeypatch.setattr(verify_bot_joined, "_capture_still_expected", lambda *_: False)

    verify_bot_joined.perform(
        MEETING_ID,
        occurrence_digest="occurrence-a",
        meeting_record_id=MEETING_RECORD_ID,
    )

    assert captured_alerts == []


def test_a_check_for_the_current_occurrence_still_alerts(redis, captured_alerts):
    _live_session(redis)
    redis.set(f"{PREFIX}:occurrence_digest", "occurrence-a")
    redis.sadd(f"{PREFIX}:participants_present", "patient-a")

    verify_bot_joined.perform(MEETING_ID, occurrence_digest="occurrence-a")

    assert len(captured_alerts) == 1
    assert captured_alerts[0]["type"] is alerting.StuckHoldingForDoctor


def test_durable_check_fences_a_mismatched_record(redis, captured_alerts, monkeypatch):
    _live_session(redis)
    redis.set(f"{PREFIX}:occurrence_digest", "occurrence-a")
    redis.sadd(f"{PREFIX}:participants_present", "patient-a")
    monkeypatch.setattr(verify_bot_joined, "_capture_still_expected", lambda *_: False)

    verify_bot_joined.perform(
        MEETING_ID,
        occurrence_digest="occurrence-a",
        meeting_record_id=MEETING_RECORD_ID,
    )

    assert captured_alerts == []
    assert redis.get(_alert_key("occurrence-a")) is None


def test_final_check_evaluates_state_restored_under_lock(
    redis, captured_alerts, monkeypatch
):
    redis.sadd(f"{PREFIX}:participants_present", "patient-a")

    class _RestoringLock:
        def acquire(self, blocking=True):
            _live_session(redis)
            redis.set(f"{PREFIX}:occurrence_digest", "occurrence-a")
            return True

        def release(self):
            pass

    monkeypatch.setattr(redis, "lock", lambda *a, **kw: _RestoringLock())
    verify_bot_joined.perform(
        MEETING_ID, check_number=5, occurrence_digest="occurrence-a"
    )

    assert captured_alerts[0]["type"] is alerting.StuckHoldingForDoctor


def test_no_alert_when_the_state_settles_before_the_verdict(redis, captured_alerts):
    _live_session(redis)
    redis.sadd(f"{PREFIX}:participants_present", "patient-a")

    real_lock = redis.lock

    class _LockThatWaitsForAHandler:
        def __init__(self, inner):
            self._inner = inner

        def acquire(self, blocking=True):
            acquired = self._inner.acquire(blocking=blocking)
            redis.set(f"{PREFIX}:recording_active", "1")
            return acquired

        def release(self):
            self._inner.release()

    redis.lock = lambda *a, **kw: _LockThatWaitsForAHandler(real_lock(*a, **kw))
    try:
        verify_bot_joined.perform(MEETING_ID)
    finally:
        redis.lock = real_lock

    assert captured_alerts == []
    assert redis.get(_alert_key()) is None


def test_alert_reason_is_reread_after_taking_the_lifecycle_lock(redis, captured_alerts):
    _live_session(redis)
    redis.sadd(f"{PREFIX}:participants_present", "patient-a")
    real_lock = redis.lock

    class _LockAfterDoctorJoins:
        def __init__(self, inner):
            self._inner = inner

        def acquire(self, blocking=True):
            acquired = self._inner.acquire(blocking=blocking)
            redis.set(f"{PREFIX}:doctor_present", "1")
            return acquired

        def release(self):
            self._inner.release()

    redis.lock = lambda *a, **kw: _LockAfterDoctorJoins(real_lock(*a, **kw))
    try:
        verify_bot_joined.perform(MEETING_ID)
    finally:
        redis.lock = real_lock

    assert captured_alerts[0]["type"] is alerting.NoRecordingWithBothPresent


def test_a_busy_lifecycle_defers_instead_of_guessing(
    redis, captured_alerts, monkeypatch
):
    _live_session(redis)
    redis.sadd(f"{PREFIX}:participants_present", "patient-a")

    class _BusyLock:
        def acquire(self, blocking=True):
            return False

        def release(self):
            pass

    monkeypatch.setattr(redis, "lock", lambda *a, **kw: _BusyLock())

    verify_bot_joined.perform(MEETING_ID)

    assert captured_alerts == []
    assert redis.get(_alert_key()) is None


def test_durable_queue_publishes_only_an_opaque_delivery_id(monkeypatch):
    monkeypatch.setattr(verify_bot_joined, "_delayed_execution_available", lambda: True)
    published: list[tuple[str, int | None]] = []
    monkeypatch.setattr(
        external_webhook_inbox,
        "publish_delivery",
        lambda delivery_id, *, countdown=None: published.append(
            (delivery_id, countdown)
        ),
    )

    stored = verify_bot_joined.queue_durably(
        MEETING_RECORD_ID, MEETING_ID, "a" * 64, check_number=2
    )

    assert stored is not None
    assert published == [
        (stored.delivery_id, verify_bot_joined.RECHECK_INTERVAL_SECONDS)
    ]
    assert MEETING_ID not in repr(published)


def test_rearm_persists_the_next_check(monkeypatch):
    monkeypatch.setattr(verify_bot_joined, "_delayed_execution_available", lambda: True)
    armed: list[tuple[str, str, str, int]] = []
    monkeypatch.setattr(
        verify_bot_joined,
        "queue_durably",
        lambda record, meeting, occurrence, *, check_number: armed.append(
            (record, meeting, occurrence, check_number)
        ),
    )

    verify_bot_joined._rearm(MEETING_RECORD_ID, MEETING_ID, 2, "occurrence-a")

    assert armed == [(MEETING_RECORD_ID, MEETING_ID, "occurrence-a", 3)]
