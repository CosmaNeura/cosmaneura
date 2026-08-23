from threading import Event, Thread

import pytest

from app.configuration.redis import get_redis

MEETING_ID = "test-mtg-mw"


@pytest.fixture
def clean_redis():
    redis = get_redis()
    suffixes = [
        "doctor_present",
        "bot_id",
        "recording_active",
        "merge_task_id",
        "paused_at",
        "participants_order",
        "participants_names",
        "participants_present",
        "participant_event_order",
        "patient_count",
        "capture_started",
        "doctor_id",
        "host_id",
        "merge_generation",
        "merge_release_intent_id",
        "occurrence_digest",
        "lifecycle_lock",
    ]
    for s in suffixes:
        redis.delete(f"recall:meeting:{MEETING_ID}:{s}")
    yield redis
    for s in suffixes:
        redis.delete(f"recall:meeting:{MEETING_ID}:{s}")


def test_merge_window_noop_when_recording_resumed(monkeypatch, clean_redis):
    """If recording resumed before the task fires, the bot must stay
    connected — no leave_call, no state cleanup."""
    from app.jobs import merge_window

    clean_redis.set(f"recall:meeting:{MEETING_ID}:bot_id", "bot-1")
    clean_redis.set(f"recall:meeting:{MEETING_ID}:recording_active", "1")
    clean_redis.set(f"recall:meeting:{MEETING_ID}:merge_task_id", "stale-task")

    called = {"leave_call": 0, "stop_recording": 0}
    monkeypatch.setattr(
        merge_window.recall,
        "leave_call",
        lambda bid: called.update(leave_call=called["leave_call"] + 1),
    )
    monkeypatch.setattr(
        merge_window.recall,
        "stop_recording",
        lambda bid: called.update(stop_recording=called["stop_recording"] + 1),
    )

    merge_window.perform(MEETING_ID)

    assert called == {"leave_call": 0, "stop_recording": 0}
    assert clean_redis.get(f"recall:meeting:{MEETING_ID}:bot_id") == b"bot-1"
    assert clean_redis.get(f"recall:meeting:{MEETING_ID}:recording_active") == b"1"


def test_merge_window_leaves_bot_when_still_paused(monkeypatch, clean_redis):
    """recording_active=false and a bot tracked → bot was paused and never
    resumed within 10 min. Issue leave_call and clear state."""
    from app.jobs import merge_window

    clean_redis.set(f"recall:meeting:{MEETING_ID}:bot_id", "bot-1")
    clean_redis.set(f"recall:meeting:{MEETING_ID}:merge_task_id", "expired-task")
    # recording_active deliberately not set (paused state)

    left = []
    monkeypatch.setattr(
        merge_window.recall,
        "leave_call",
        lambda bid: left.append(bid),
    )
    # stop_recording should NOT be called — pause already stopped it
    stop_calls = []
    monkeypatch.setattr(
        merge_window.recall,
        "stop_recording",
        lambda bid: stop_calls.append(bid),
    )

    merge_window.perform(MEETING_ID)

    assert left == ["bot-1"]
    assert stop_calls == []
    assert clean_redis.get(f"recall:meeting:{MEETING_ID}:bot_id") is None
    assert clean_redis.get(f"recall:meeting:{MEETING_ID}:merge_task_id") is None


def test_merge_window_noop_when_no_bot_tracked(monkeypatch, clean_redis):
    """No bot in Redis → meeting ended via another path. Nothing to do."""
    from app.jobs import merge_window

    called = {"leave_call": 0}
    monkeypatch.setattr(
        merge_window.recall,
        "leave_call",
        lambda bid: called.update(leave_call=called["leave_call"] + 1),
    )

    merge_window.perform(MEETING_ID)

    assert called == {"leave_call": 0}


def test_merge_window_leave_failure_retains_recoverable_state(monkeypatch, clean_redis):
    from app.jobs import merge_window

    clean_redis.set(f"recall:meeting:{MEETING_ID}:bot_id", "bot-1")
    clean_redis.set(f"recall:meeting:{MEETING_ID}:merge_task_id", "task-1")
    clean_redis.set(f"recall:meeting:{MEETING_ID}:paused_at", "now")
    clean_redis.set(f"recall:meeting:{MEETING_ID}:merge_generation", "4")

    def fail_leave(bot_id):
        raise RuntimeError(f"failed to leave {bot_id}")

    monkeypatch.setattr(merge_window.recall, "leave_call", fail_leave)

    with pytest.raises(RuntimeError, match="failed to leave bot-1"):
        merge_window.perform(MEETING_ID, 4)

    assert clean_redis.get(f"recall:meeting:{MEETING_ID}:bot_id") == b"bot-1"
    assert clean_redis.get(f"recall:meeting:{MEETING_ID}:merge_task_id") == b"task-1"
    assert clean_redis.get(f"recall:meeting:{MEETING_ID}:paused_at") == b"now"
    assert clean_redis.get(f"recall:meeting:{MEETING_ID}:merge_generation") == b"4"


def test_stale_merge_generation_cannot_release_newer_pause(monkeypatch, clean_redis):
    from app.jobs import merge_window

    clean_redis.set(f"recall:meeting:{MEETING_ID}:bot_id", "bot-1")
    clean_redis.set(f"recall:meeting:{MEETING_ID}:merge_task_id", "task-new")
    clean_redis.set(f"recall:meeting:{MEETING_ID}:merge_generation", "2")
    left = []
    monkeypatch.setattr(merge_window.recall, "leave_call", left.append)

    merge_window.perform(MEETING_ID, 1)

    assert left == []
    assert clean_redis.get(f"recall:meeting:{MEETING_ID}:bot_id") == b"bot-1"
    assert clean_redis.get(f"recall:meeting:{MEETING_ID}:merge_task_id") == b"task-new"


def test_legacy_task_cannot_release_generation_fenced_pause(monkeypatch, clean_redis):
    from app.jobs import merge_window

    clean_redis.set(f"recall:meeting:{MEETING_ID}:bot_id", "bot-1")
    clean_redis.set(f"recall:meeting:{MEETING_ID}:merge_task_id", "task-new")
    clean_redis.set(f"recall:meeting:{MEETING_ID}:merge_generation", "1")
    left = []
    monkeypatch.setattr(merge_window.recall, "leave_call", left.append)

    # Old API revisions queued only meeting_id. New workers accept that shape
    # during the rolling deploy, but fence it once new code owns the pause.
    merge_window.perform(MEETING_ID)

    assert left == []
    assert clean_redis.get(f"recall:meeting:{MEETING_ID}:bot_id") == b"bot-1"


def test_prior_occurrence_task_cannot_release_current_bot(monkeypatch, clean_redis):
    from app.jobs import merge_window

    prefix = f"recall:meeting:{MEETING_ID}"
    clean_redis.set(f"{prefix}:bot_id", "bot-current")
    clean_redis.set(f"{prefix}:merge_generation", "1")
    clean_redis.set(f"{prefix}:occurrence_digest", "occurrence-current")
    left: list[str] = []
    monkeypatch.setattr(merge_window.recall, "leave_call", left.append)

    merge_window.perform(MEETING_ID, 1, "occurrence-prior")

    assert left == []
    assert clean_redis.get(f"{prefix}:bot_id") == b"bot-current"


def test_reconciler_repairs_pause_after_queue_failure(monkeypatch, clean_redis):
    from app.jobs import merge_window

    class FakeAsyncResult:
        id = "recovered-merge-task"

    prefix = f"recall:meeting:{MEETING_ID}"
    occurrence_digest = "a" * 64
    clean_redis.set(f"{prefix}:bot_id", "bot-1")
    clean_redis.set(f"{prefix}:recording_active", "1")
    clean_redis.set(f"{prefix}:occurrence_digest", occurrence_digest)
    stopped: list[str] = []
    monkeypatch.setattr(merge_window.recall, "stop_recording", stopped.append)
    monkeypatch.setattr(
        merge_window.expire,
        "apply_async",
        lambda *args, **kwargs: FakeAsyncResult(),
    )

    merge_window.reconcile_teardown_intent(
        "internal.zoom_bot_pause",
        {
            "teardown_intent": {
                "meeting_id": MEETING_ID,
                "bot_id": "bot-1",
                "occurrence_digest": occurrence_digest,
                "generation": 1,
                "stop_recording": True,
                "cleanup_session": False,
            }
        },
    )

    assert stopped == ["bot-1"]
    assert clean_redis.get(f"{prefix}:recording_active") is None
    assert clean_redis.get(f"{prefix}:merge_generation") == b"1"
    assert clean_redis.get(f"{prefix}:merge_task_id") == b"recovered-merge-task"


def test_reconciler_releases_durable_bot_after_redis_state_loss(
    monkeypatch, clean_redis
):
    from app.jobs import merge_window

    stopped: list[str] = []
    left: list[str] = []
    monkeypatch.setattr(merge_window.recall, "stop_recording", stopped.append)
    monkeypatch.setattr(merge_window.recall, "leave_call", left.append)

    merge_window.reconcile_teardown_intent(
        "internal.zoom_bot_release",
        {
            "teardown_intent": {
                "meeting_id": MEETING_ID,
                "bot_id": "orphaned-bot",
                "occurrence_digest": "lost-occurrence",
                "generation": 1,
                "stop_recording": True,
                "cleanup_session": True,
            }
        },
    )

    assert stopped == ["orphaned-bot"]
    assert left == ["orphaned-bot"]


def test_continuation_is_blocked_while_paused_bot_is_retained(monkeypatch, clean_redis):
    from app.services import zoom_webhooks

    prefix = f"recall:meeting:{MEETING_ID}"
    clean_redis.set(f"{prefix}:bot_id", "bot-1")
    clean_redis.set(f"{prefix}:merge_task_id", "task-1")
    clean_redis.set(f"{prefix}:paused_at", "now")
    clean_redis.set(f"{prefix}:merge_generation", "1")
    clean_redis.set(f"{prefix}:capture_started", "1")
    restarted: list[str] = []
    started_new: list[tuple[str, str, str]] = []
    monkeypatch.setattr(zoom_webhooks.recall, "start_recording", restarted.append)
    monkeypatch.setattr(
        zoom_webhooks,
        "_start_appointment_session",
        lambda meeting_id, participant_id, user_name: started_new.append(
            (meeting_id, participant_id, user_name)
        ),
    )

    zoom_webhooks._resume_or_start_recording(MEETING_ID, "patient-2", "P")

    assert restarted == []
    assert started_new == []
    assert clean_redis.get(f"{prefix}:bot_id") == b"bot-1"
    assert clean_redis.get(f"{prefix}:recording_active") is None
    assert clean_redis.get(f"{prefix}:merge_task_id") == b"task-1"


def test_expiry_wins_race_and_resume_never_restarts_released_bot(
    monkeypatch, clean_redis
):
    from app.jobs import merge_window
    from app.services import zoom_webhooks

    prefix = f"recall:meeting:{MEETING_ID}"
    clean_redis.set(f"{prefix}:bot_id", "bot-1")
    clean_redis.set(f"{prefix}:merge_task_id", "task-1")
    clean_redis.set(f"{prefix}:paused_at", "now")
    clean_redis.set(f"{prefix}:merge_generation", "1")
    clean_redis.set(f"{prefix}:capture_started", "1")

    leave_entered = Event()
    allow_leave = Event()
    errors: list[BaseException] = []
    restarted_old: list[str] = []
    started_new: list[tuple[str, str, str]] = []

    def leave_call(bot_id):
        leave_entered.set()
        assert allow_leave.wait(timeout=3)

    monkeypatch.setattr(merge_window.recall, "leave_call", leave_call)
    monkeypatch.setattr(
        zoom_webhooks.recall,
        "start_recording",
        lambda bot_id: restarted_old.append(bot_id),
    )
    monkeypatch.setattr(
        zoom_webhooks,
        "_start_appointment_session",
        lambda meeting_id, participant_id, user_name: started_new.append(
            (meeting_id, participant_id, user_name)
        ),
    )

    def expire():
        try:
            merge_window.perform(MEETING_ID, 1)
        except BaseException as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    def resume():
        try:
            zoom_webhooks._resume_or_start_recording(MEETING_ID, "patient-1", "P")
        except BaseException as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    expiry_thread = Thread(target=expire)
    resume_thread = Thread(target=resume)
    expiry_thread.start()
    assert leave_entered.wait(timeout=3)
    resume_thread.start()
    allow_leave.set()
    expiry_thread.join(timeout=3)
    resume_thread.join(timeout=3)

    assert not expiry_thread.is_alive()
    assert not resume_thread.is_alive()
    assert errors == []
    assert restarted_old == []
    assert started_new == []
    assert clean_redis.get(f"{prefix}:capture_started") == b"1"
