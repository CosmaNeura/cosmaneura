"""Join-watchdog safety and failure tests."""

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
    "occurrence_digest",
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


def test_no_alert_when_meeting_state_is_gone(redis, captured_alerts):
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
    _live_session(redis)
    redis.set(f"{PREFIX}:capture_started", "1")
    redis.set(f"{PREFIX}:doctor_present", "1")
    redis.sadd(f"{PREFIX}:participants_present", "patient-a")

    verify_bot_joined.perform(MEETING_ID)

    assert captured_alerts == []


def test_no_alert_when_paused_inside_merge_window(redis, captured_alerts):
    _live_session(redis)
    redis.set(f"{PREFIX}:doctor_present", "1")
    redis.sadd(f"{PREFIX}:participants_present", "patient-a")
    redis.set(f"{PREFIX}:paused_at", "2026-09-01T12:00:00")

    verify_bot_joined.perform(MEETING_ID)

    assert captured_alerts == []


def test_no_alert_when_the_doctor_is_alone(redis, captured_alerts):
    _live_session(redis)
    redis.set(f"{PREFIX}:doctor_present", "1")

    verify_bot_joined.perform(MEETING_ID)

    assert captured_alerts == []


def test_no_alert_for_an_empty_room(redis, captured_alerts):
    _live_session(redis)

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

    verify_bot_joined.perform(MEETING_ID)

    assert len(captured_alerts) == 1
    assert captured_alerts[0]["type"] is alerting.NoRecordingWithBothPresent
    assert captured_alerts[0]["patient_count"] == 1
    assert captured_alerts[0]["doctor_present"] is True


def test_alerts_at_most_once_per_meeting(redis, captured_alerts):
    _live_session(redis)
    redis.set(f"{PREFIX}:doctor_present", "1")
    redis.sadd(f"{PREFIX}:participants_present", "patient-a")

    verify_bot_joined.perform(MEETING_ID, check_number=1)
    verify_bot_joined.perform(MEETING_ID, check_number=2)
    verify_bot_joined.perform(MEETING_ID, check_number=3)

    assert len(captured_alerts) == 1


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
    assert redis.get(f"{PREFIX}:watchdog_alerted") is None

    verify_bot_joined.perform(MEETING_ID)
    assert attempts == [True, True]
    assert redis.get(f"{PREFIX}:watchdog_alerted") is not None


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
        if key == f"{PREFIX}:watchdog_alerted":
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


def test_occupancy_counts_people_not_webhooks(redis, captured_alerts):
    _live_session(redis)
    redis.sadd(f"{PREFIX}:participants_present", "patient-a")
    redis.sadd(f"{PREFIX}:participants_present", "patient-a")
    redis.set(f"{PREFIX}:doctor_present", "1")

    verify_bot_joined.perform(MEETING_ID)

    assert captured_alerts[0]["patient_count"] == 1


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


def test_task_is_registered_under_the_name_beat_and_revocation_use():
    from app.celery import celery_app

    assert "app.jobs.verify_bot_joined.perform" in celery_app.tasks


def test_meeting_started_arms_the_watchdog_through_this_module():
    from app.services import zoom_webhooks

    assert zoom_webhooks.verify_bot_joined is verify_bot_joined


def test_a_check_from_a_previous_occurrence_stands_down(redis, captured_alerts):
    _live_session(redis)
    redis.set(f"{PREFIX}:occurrence_digest", "occurrence-b")
    redis.sadd(f"{PREFIX}:participants_present", "patient-a")

    verify_bot_joined.perform(MEETING_ID, occurrence_digest="occurrence-a")

    assert captured_alerts == []


def test_a_check_for_the_current_occurrence_still_alerts(redis, captured_alerts):
    _live_session(redis)
    redis.set(f"{PREFIX}:occurrence_digest", "occurrence-a")
    redis.sadd(f"{PREFIX}:participants_present", "patient-a")

    verify_bot_joined.perform(MEETING_ID, occurrence_digest="occurrence-a")

    assert len(captured_alerts) == 1
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
    assert redis.get(f"{PREFIX}:watchdog_alerted") is None


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
    assert redis.get(f"{PREFIX}:watchdog_alerted") is None


def test_the_watchdog_takes_the_same_lock_the_handlers_take():
    from app.services.zoom_webhooks import lifecycle_lock_key

    assert lifecycle_lock_key("m-1") == "recall:meeting:m-1:lifecycle_lock"


def test_publish_failure_releases_claim_so_retry_can_arm(redis, monkeypatch):
    monkeypatch.setattr(verify_bot_joined, "_delayed_execution_available", lambda: True)
    published: list[tuple[tuple, dict]] = []

    def publish(*args, **kwargs):
        published.append((args, kwargs))
        if len(published) == 1:
            raise RuntimeError("broker unavailable")
        return "queued"

    monkeypatch.setattr(verify_bot_joined.perform_celery, "apply_async", publish)

    with pytest.raises(RuntimeError, match="broker unavailable"):
        verify_bot_joined.queue(MEETING_ID, occurrence_digest="occurrence-a")

    assert redis.get(f"{PREFIX}:watchdog_queued") is None
    assert (
        verify_bot_joined.queue(MEETING_ID, occurrence_digest="occurrence-a")
        == "queued"
    )
    assert published == [
        (
            (),
            {
                "args": (MEETING_ID, 1, "occurrence-a"),
                "countdown": verify_bot_joined.FIRST_CHECK_DELAY_SECONDS,
            },
        ),
        (
            (),
            {
                "args": (MEETING_ID, 1, "occurrence-a"),
                "countdown": verify_bot_joined.FIRST_CHECK_DELAY_SECONDS,
            },
        ),
    ]
    assert redis.get(f"{PREFIX}:watchdog_queued") is not None


def test_retried_claim_with_our_token_still_publishes(redis, monkeypatch):
    monkeypatch.setattr(verify_bot_joined, "_delayed_execution_available", lambda: True)
    monkeypatch.setattr(verify_bot_joined.secrets, "token_hex", lambda _size: "ours")
    redis.set(f"{PREFIX}:watchdog_queued", "ours", ex=60)
    published: list[bool] = []
    monkeypatch.setattr(
        verify_bot_joined.perform_celery,
        "apply_async",
        lambda **_kwargs: published.append(True) or "queued",
    )

    assert verify_bot_joined.queue(MEETING_ID) == "queued"
    assert published == [True]


def test_foreign_provisional_claim_requires_retry(redis, monkeypatch):
    monkeypatch.setattr(verify_bot_joined, "_delayed_execution_available", lambda: True)
    redis.set(
        f"{PREFIX}:watchdog_queued",
        "another-publisher",
        ex=verify_bot_joined._QUEUE_CLAIM_TTL_SECONDS,
    )

    with pytest.raises(verify_bot_joined.WatchdogQueueClaimPending):
        verify_bot_joined.queue(MEETING_ID)


def test_publish_failure_never_releases_a_replacement_claim(redis, monkeypatch):
    monkeypatch.setattr(verify_bot_joined, "_delayed_execution_available", lambda: True)
    queue_key = f"{PREFIX}:watchdog_queued"

    def publish(*_args, **_kwargs):
        redis.delete(queue_key)
        assert redis.set(queue_key, "replacement-owner", nx=True, ex=60)
        raise RuntimeError("original publisher failed")

    monkeypatch.setattr(verify_bot_joined.perform_celery, "apply_async", publish)

    with pytest.raises(RuntimeError, match="original publisher failed"):
        verify_bot_joined.queue(MEETING_ID, occurrence_digest="occurrence-a")

    assert redis.get(queue_key) == b"replacement-owner"


def test_successful_publish_retains_claim_and_deduplicates(redis, monkeypatch):
    monkeypatch.setattr(verify_bot_joined, "_delayed_execution_available", lambda: True)
    published: list[bool] = []

    def publish(*_args, **_kwargs):
        published.append(True)
        return "queued"

    monkeypatch.setattr(verify_bot_joined.perform_celery, "apply_async", publish)

    assert verify_bot_joined.queue(MEETING_ID) == "queued"
    assert verify_bot_joined.queue(MEETING_ID) is None
    assert published == [True]
    assert redis.get(f"{PREFIX}:watchdog_queued") is not None
    assert (
        redis.ttl(f"{PREFIX}:watchdog_queued")
        > verify_bot_joined._QUEUE_CLAIM_TTL_SECONDS
    )


def test_cleanup_failure_leaves_only_a_short_retry_suppression(redis, monkeypatch):
    monkeypatch.setattr(verify_bot_joined, "_delayed_execution_available", lambda: True)

    def fail_publish(**_kwargs):
        monkeypatch.setattr(
            redis, "eval", lambda *_args: (_ for _ in ()).throw(ConnectionError())
        )
        raise RuntimeError("broker unavailable")

    monkeypatch.setattr(
        verify_bot_joined.perform_celery,
        "apply_async",
        fail_publish,
    )

    with pytest.raises(RuntimeError, match="broker unavailable"):
        verify_bot_joined.queue(MEETING_ID)

    ttl = redis.ttl(f"{PREFIX}:watchdog_queued")
    assert 0 < ttl <= verify_bot_joined._QUEUE_CLAIM_TTL_SECONDS


def test_finalize_failure_keeps_a_short_claim_and_raises(redis, monkeypatch):
    monkeypatch.setattr(verify_bot_joined, "_delayed_execution_available", lambda: True)
    real_eval = redis.eval

    def fail_finalize(script, *args):
        if script == verify_bot_joined._FINALIZE_OWNED_QUEUE_CLAIM:
            raise ConnectionError()
        return real_eval(script, *args)

    published: list[bool] = []
    monkeypatch.setattr(redis, "eval", fail_finalize)
    monkeypatch.setattr(
        verify_bot_joined.perform_celery,
        "apply_async",
        lambda **_kwargs: published.append(True),
    )

    with pytest.raises(ConnectionError):
        verify_bot_joined.queue(MEETING_ID)
    assert published == [True]
    assert 0 < redis.ttl(f"{PREFIX}:watchdog_queued") <= 300
