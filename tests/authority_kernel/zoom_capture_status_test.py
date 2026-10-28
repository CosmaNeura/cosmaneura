"""Signed DTOs, real isolated Redis Lua, no provider or clinical data access."""

import json
from types import SimpleNamespace

import pytest

from app.services import recall_webhooks
from app.services import zoom_capture_status as status
from app.services.external_webhook_inbox import (
    PermanentWebhookError,
    RetryableWebhookError,
)

from tests.authority_kernel.zoom_launch_boundary_test import fenced as launch_fixture
from tests.authority_kernel.zoom_launch_boundary_test import publish

fenced = launch_fixture


def event(
    f,
    code="recording_permission_denied",
    *,
    sub="zoom_local_recording_request_denied_by_host",
    at="2026-10-26T20:21:43.864389Z",
):
    return {
        "event": "bot." + code,
        "data": {
            "bot": {"id": f.bot, "metadata": {"doctor_id": "untrusted"}},
            "data": {"code": code, "sub_code": sub, "updated_at": at},
        },
    }


@pytest.fixture
def active(fenced, monkeypatch):
    publish(fenced)
    monkeypatch.setattr(status, "get_redis", lambda: fenced.redis)
    return fenced


def result(f):
    return json.loads(f.redis.get(f.prefix + ":capture_terminal"))


def test_actual_denial_then_done_keeps_failure_and_single_start_fence(active):
    f = active
    before = f.redis.mget(
        [
            f"recall:recording:{f.recording}:{s}"
            for s in ("doctor_id", "appointment_id", "patient_id")
        ]
    )
    for code, at in [
        ("recording_permission_denied", "2026-10-26T20:21:43.864389Z"),
        ("call_ended", "2026-10-26T20:22:14.915215Z"),
        ("done", "2026-10-26T20:22:15.710802Z"),
    ]:
        recall_webhooks.dispatch(
            event_type="bot." + code, payload=event(f, code, at=at)
        )
    assert result(f)["status"] == "permission_denied"
    assert f.redis.get(f.prefix + ":recording_active") is None
    assert f.redis.get(f.prefix + ":capture_started") == "1"
    assert f.redis.get(f.prefix + ":bot_id") == f.bot
    assert (
        f.redis.mget(
            [
                f"recall:recording:{f.recording}:{s}"
                for s in ("doctor_id", "appointment_id", "patient_id")
            ]
        )
        == before
    )
    with pytest.raises(ValueError, match="lock_lost"):
        f.boundary.require()


def test_out_of_order_duplicate_denial_is_not_hidden_by_done(active):
    f = active
    status.handle_bot_terminal(event(f, "done", at="2026-10-26T20:22:15.710802Z"))
    status.handle_bot_terminal(event(f))
    first = result(f)
    status.handle_bot_terminal(event(f))
    assert result(f) == first
    assert first["status"] == "permission_denied"
    assert first["updated_at"] == "2026-10-26T20:22:15.710802+00:00"


def test_transient_zoom_handshake_does_not_clear_active(active):
    status.handle_bot_terminal(
        event(active, sub="zoom_local_recording_request_disabled")
    )
    assert active.redis.get(active.prefix + ":recording_active") == "1"
    assert active.redis.get(active.prefix + ":capture_terminal") is None


@pytest.mark.parametrize("change", ["doctor_id", "occurrence_digest", "bot_id"])
def test_stale_event_cannot_touch_successor_capture(active, change):
    f = active
    f.redis.set(f.prefix + ":" + change, "successor")
    status.handle_bot_terminal(event(f))
    assert f.redis.get(f.prefix + ":recording_active") == "1"
    assert f.redis.get(f.prefix + ":capture_terminal") is None


def test_binding_change_between_read_and_lua_is_fenced(active, monkeypatch):
    f = active
    execute = f.redis.eval

    def raced(*args):
        f.redis.set(f"recall:bot:{f.bot}:zoom_capture", "changed")
        return execute(*args)

    monkeypatch.setattr(f.redis, "eval", raced)
    status.handle_bot_terminal(event(f))
    assert f.redis.get(f.prefix + ":recording_active") == "1"


def test_unknown_bot_never_uses_provider_metadata(active):
    active.redis.delete(f"recall:bot:{active.bot}:zoom_capture")
    with pytest.raises(RetryableWebhookError):
        status.handle_bot_terminal(event(active))
    assert active.redis.get(active.prefix + ":recording_active") == "1"


@pytest.mark.parametrize("mutation", ["event", "code", "bot", "timestamp", "timezone"])
def test_malformed_dto_refuses_without_projection(active, mutation):
    value = event(active)
    if mutation == "event":
        value["event"] = {}
    elif mutation == "code":
        value["data"]["data"]["code"] = "done"
    elif mutation == "bot":
        value["data"]["bot"]["id"] = "../other"
    elif mutation == "timestamp":
        value["data"]["data"]["updated_at"] = None
    else:
        value["data"]["data"]["updated_at"] = "2026-10-26T20:22:15"
    with pytest.raises(PermanentWebhookError):
        status.handle_bot_terminal(value)
    assert active.redis.get(active.prefix + ":recording_active") == "1"


def test_denial_before_publication_prevents_active_state(fenced, monkeypatch):
    f = fenced
    f.boundary.publish_bot(f.bot)
    monkeypatch.setattr(status, "get_redis", lambda: f.redis)
    status.handle_bot_terminal(event(f))
    # Even if a require/preflight was already passed, Lua refuses publication.
    monkeypatch.setattr(f.boundary, "require", lambda *_: None)
    with pytest.raises(ValueError, match="publication_refused"):
        f.boundary.publish_recording(f.bot, f.recording, f.appointment, f.patient)
    assert not f.redis.exists(f.prefix + ":capture_started")
    assert not f.redis.exists(f"recall:recording:{f.recording}:doctor_id")


@pytest.mark.parametrize(
    "code,expected", [("call_ended", "ended"), ("done", "ended"), ("fatal", "failed")]
)
def test_terminal_events_clear_only_current_active_state(active, code, expected):
    status.handle_bot_terminal(event(active, code))
    assert result(active)["status"] == expected
    assert active.redis.get(active.prefix + ":capture_started") == "1"
    assert active.redis.get(active.prefix + ":recording_active") is None


def test_schedule_status_is_scoped_to_consumed_occurrence_and_survives_cleanup(active):
    f = active
    status.handle_bot_terminal(event(f))
    m = SimpleNamespace(
        zoom_meeting_id=f.meeting,
        doctor_id=f.doctor,
        capture_authorization_consumed_at=object(),
        capture_authorization_occurrence_digest=f.occurrence,
    )
    assert status.for_meeting(m) == "permission_denied"
    f.redis.delete(
        f.prefix + ":bot_id", f.prefix + ":doctor_id", f.prefix + ":occurrence_digest"
    )
    assert status.for_meeting(m) == "permission_denied"
    m.doctor_id = "other"
    assert status.for_meeting(m) is None
    m.doctor_id = f.doctor
    m.capture_authorization_occurrence_digest = "next"
    assert status.for_meeting(m) is None
    m.capture_authorization_occurrence_digest = f.occurrence
    m.capture_authorization_consumed_at = None
    assert status.for_meeting(m) is None
