import pytest

from app.configuration.redis import get_redis

MEETING_ID = "test-mtg-mw"


@pytest.fixture
def clean_redis():
    redis = get_redis()
    suffixes = [
        "doctor_present", "bot_id", "recording_active",
        "merge_task_id", "paused_at",
        "participants_order", "participants_names", "patient_count",
        "doctor_id", "host_id",
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
        merge_window.recall, "leave_call",
        lambda bid: called.update(leave_call=called["leave_call"] + 1),
    )
    monkeypatch.setattr(
        merge_window.recall, "stop_recording",
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
        merge_window.recall, "leave_call",
        lambda bid: left.append(bid),
    )
    # stop_recording should NOT be called — pause already stopped it
    stop_calls = []
    monkeypatch.setattr(
        merge_window.recall, "stop_recording",
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
        merge_window.recall, "leave_call",
        lambda bid: called.update(leave_call=called["leave_call"] + 1),
    )

    merge_window.perform(MEETING_ID)

    assert called == {"leave_call": 0}
