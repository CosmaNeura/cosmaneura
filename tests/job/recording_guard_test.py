import pytest

from app.configuration.redis import get_redis


@pytest.fixture
def clean_redis():
    redis = get_redis()
    # Nuke test keys for meeting_id "test-mtg-1"
    for suffix in [
        "doctor_present", "bot_id", "recording_active",
        "grace_task_id", "participants_order", "participants_names",
    ]:
        redis.delete(f"recall:meeting:test-mtg-1:{suffix}")
    yield redis
    for suffix in [
        "doctor_present", "bot_id", "recording_active",
        "grace_task_id", "participants_order", "participants_names",
    ]:
        redis.delete(f"recall:meeting:test-mtg-1:{suffix}")


def test_grace_task_aborts_when_doctor_back(monkeypatch, clean_redis):
    from app.jobs import recording_guard

    clean_redis.set("recall:meeting:test-mtg-1:doctor_present", "1")
    clean_redis.set("recall:meeting:test-mtg-1:bot_id", "bot-1")
    clean_redis.set("recall:meeting:test-mtg-1:recording_active", "1")

    called = {"stop": 0, "leave": 0}

    def fake_stop(bot_id): called["stop"] += 1
    def fake_leave(bot_id): called["leave"] += 1

    monkeypatch.setattr(recording_guard.recall, "stop_recording", fake_stop)
    monkeypatch.setattr(recording_guard.recall, "leave_call", fake_leave)

    recording_guard.stop_recording_if_absent("test-mtg-1")

    assert called == {"stop": 0, "leave": 0}
    # Redis state unchanged
    assert clean_redis.get("recall:meeting:test-mtg-1:recording_active") == b"1"


def test_grace_task_stops_and_leaves_when_doctor_still_absent(monkeypatch, clean_redis):
    from app.jobs import recording_guard

    # doctor_present NOT set — simulates doctor still gone
    clean_redis.set("recall:meeting:test-mtg-1:bot_id", "bot-1")
    clean_redis.set("recall:meeting:test-mtg-1:recording_active", "1")

    called = {"stop": [], "leave": []}
    monkeypatch.setattr(
        recording_guard.recall, "stop_recording",
        lambda bid: called["stop"].append(bid),
    )
    monkeypatch.setattr(
        recording_guard.recall, "leave_call",
        lambda bid: called["leave"].append(bid),
    )

    recording_guard.stop_recording_if_absent("test-mtg-1")

    assert called["stop"] == ["bot-1"]
    assert called["leave"] == ["bot-1"]
    # Redis cleaned
    assert clean_redis.get("recall:meeting:test-mtg-1:recording_active") is None
    assert clean_redis.get("recall:meeting:test-mtg-1:bot_id") is None


def test_grace_task_noop_when_no_bot(monkeypatch, clean_redis):
    from app.jobs import recording_guard

    called = {"stop": 0, "leave": 0}
    monkeypatch.setattr(
        recording_guard.recall, "stop_recording",
        lambda bid: called.update(stop=called["stop"] + 1),
    )
    monkeypatch.setattr(
        recording_guard.recall, "leave_call",
        lambda bid: called.update(leave=called["leave"] + 1),
    )

    # No keys set at all
    recording_guard.stop_recording_if_absent("test-mtg-1")

    assert called == {"stop": 0, "leave": 0}
