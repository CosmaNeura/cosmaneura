import httpx
import pytest

from app.services import recall


def test_leave_call_posts_to_bot_endpoint(monkeypatch):
    captured = {}

    class FakeResponse:
        status_code = 200
        text = '{"ok": true}'

        @property
        def is_success(self):
            return True

        def raise_for_status(self):
            return None

    class FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url, headers=None):
            captured["url"] = url
            captured["headers"] = headers
            return FakeResponse()

    monkeypatch.setattr(httpx, "Client", lambda **_: FakeClient())

    recall.leave_call("bot-abc-123")

    assert captured["url"].endswith("/bot/bot-abc-123/leave_call/")
    assert "Authorization" in captured["headers"]


def test_leave_call_tolerates_already_done_bot(monkeypatch):
    class FakeResponse:
        status_code = 400
        text = "bot already done"

        @property
        def is_success(self):
            return False

        def raise_for_status(self):
            req = httpx.Request("POST", "http://test/")
            raise httpx.HTTPStatusError(
                "", request=req, response=httpx.Response(400, request=req)
            )

    class FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url, headers=None):
            return FakeResponse()

    monkeypatch.setattr(httpx, "Client", lambda **_: FakeClient())

    # Should NOT raise — 400 means bot already terminated
    recall.leave_call("bot-abc-123")


def test_create_bot_keeps_manual_recording_and_forwards_metadata(monkeypatch):
    captured = {}

    class FakeResponse:
        status_code = 200
        text = '{"id": "bot-xyz"}'

        @property
        def is_success(self):
            return True

        def raise_for_status(self):
            return None

        def json(self):
            return {"id": "bot-xyz"}

    class FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url, json=None, headers=None):
            captured["payload"] = json
            return FakeResponse()

    monkeypatch.setattr(httpx, "Client", lambda **_: FakeClient())

    recall.create_bot(
        meeting_url="https://zoom.us/j/123",
        zak_url="https://app.cosmaneura.com/external/v1/recall/zak/abc",
        metadata={"doctor_id": "doctor_x", "meeting_id": "m1"},
    )

    payload = captured["payload"]
    assert payload["metadata"] == {
        "doctor_id": "doctor_x",
        "meeting_id": "m1",
    }
    assert payload["bot_name"] == "Cosmas AI Note-Taker"
    # Recall's Create Bot payload must remain null so creation never starts a
    # recording. Start Recording supplies the full retention configuration.
    assert payload["recording_config"] is None
    zoom_cfg = payload["zoom"]
    assert zoom_cfg["zak_url"] == (
        "https://app.cosmaneura.com/external/v1/recall/zak/abc"
    )
    assert zoom_cfg["require_recording_permission"] is True
    assert zoom_cfg["request_recording_permission_on_host_join"] is True
    assert "zak_token" not in zoom_cfg


def test_create_bot_rejects_automatic_recording(monkeypatch):
    def fail_client(**_):
        raise AssertionError("Recall must not be called with automatic recording")

    monkeypatch.setattr(httpx, "Client", fail_client)

    with pytest.raises(ValueError, match="manual recording"):
        recall.create_bot(
            meeting_url="https://zoom.us/j/123",
            zak_url="https://app.cosmaneura.com/external/v1/recall/zak/abc",
            manual_recording=False,
        )


def test_start_recording_sends_exact_six_hour_retention(monkeypatch):
    captured = {}

    class FakeResponse:
        status_code = 200

        @property
        def is_success(self):
            return True

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "id": "bot-xyz",
                "status_changes": [{"code": "in_call_recording"}],
                "recordings": [
                    {
                        "id": "recording-xyz",
                        "started_at": "2026-10-25T10:00:00Z",
                        "completed_at": None,
                        "status": {"code": "processing"},
                    }
                ],
            }

    class FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url, json=None, headers=None):
            captured["url"] = url
            captured["payload"] = json
            return FakeResponse()

    monkeypatch.setattr(httpx, "Client", lambda **_: FakeClient())

    monkeypatch.setattr(
        recall,
        "_read_recording_bot",
        lambda *_: {
            "id": "bot-xyz",
            "recordings": [],
            "status_changes": [{"code": "in_call_not_recording"}],
        },
    )

    assert recall.start_recording("bot-xyz", wait_for_ready=False) == {
        "id": "recording-xyz"
    }
    assert captured["url"].endswith("/bot/bot-xyz/start_recording/")
    assert captured["payload"] == {"retention": {"type": "timed", "hours": 6}}


def _changes(*codes: str | tuple[str, str]) -> dict:
    "Build a get_bot payload from status codes or (code, sub_code) pairs."
    return {
        "status_changes": [
            {"code": c, "sub_code": None}
            if isinstance(c, str)
            else {"code": c[0], "sub_code": c[1]}
            for c in codes
        ]
    }


def _stub_polls(monkeypatch, payloads: list[dict]) -> None:
    "Serve one payload per get_bot call, repeating the last one."
    calls = {"n": 0}

    def fake_get_bot(bot_id: str) -> dict:
        payload = payloads[min(calls["n"], len(payloads) - 1)]
        calls["n"] += 1
        return payload

    monkeypatch.setattr(recall, "get_bot", fake_get_bot)
    monkeypatch.setattr(recall.time, "sleep", lambda _: None)


def test_wait_until_ready_survives_transient_permission_denied(monkeypatch):
    """Regression: the Zoom consent handshake passes *through*
    recording_permission_denied on its way to approval on every session. A
    poll landing in that ~600ms window used to abort a healthy bot."""
    _stub_polls(
        monkeypatch,
        [
            _changes("joining_call"),
            # This is the exact production sequence that broke prod bots.
            _changes(
                "joining_call",
                "in_call_not_recording",
                (
                    "recording_permission_denied",
                    "zoom_local_recording_request_disabled",
                ),
            ),
            _changes(
                "joining_call",
                "in_call_not_recording",
                (
                    "recording_permission_denied",
                    "zoom_local_recording_request_disabled",
                ),
                "recording_permission_allowed",
            ),
        ],
    )

    status = recall._wait_until_bot_ready("bot-1", timeout_seconds=10)

    assert status == "recording_permission_allowed"
    assert status in recall._BOT_READY_STATUSES


def test_transient_permission_phase_polls_faster_than_normal_interval(monkeypatch):
    """Approval lands ~600ms after the transient denial. Sleeping the full 2s
    interval there would start recording late and clip the opening audio."""
    slept: list[float] = []

    _stub_polls(
        monkeypatch,
        [
            _changes(
                "joining_call",
                "in_call_not_recording",
                (
                    "recording_permission_denied",
                    "zoom_local_recording_request_disabled",
                ),
            ),
            _changes(
                "joining_call",
                "in_call_not_recording",
                (
                    "recording_permission_denied",
                    "zoom_local_recording_request_disabled",
                ),
                "recording_permission_allowed",
            ),
        ],
    )
    monkeypatch.setattr(recall.time, "sleep", slept.append)

    status = recall._wait_until_bot_ready("bot-6", timeout_seconds=10)

    assert status == "recording_permission_allowed"
    assert slept == [recall._PERMISSION_PHASE_POLL_INTERVAL]
    assert recall._PERMISSION_PHASE_POLL_INTERVAL < 2.0


def test_wait_until_ready_treats_real_denial_as_terminal(monkeypatch):
    "A denial that isn't the transient Zoom sub_code is still terminal."
    _stub_polls(
        monkeypatch,
        [
            _changes(
                "joining_call",
                "in_call_not_recording",
                ("recording_permission_denied", "host_denied_recording_request"),
            )
        ],
    )

    status = recall._wait_until_bot_ready("bot-2", timeout_seconds=10)

    assert status == "recording_permission_denied"
    assert status not in recall._BOT_READY_STATUSES


def test_wait_until_ready_reads_transitions_missed_between_polls(monkeypatch):
    """The bot can pass through several states inside one 2s poll interval;
    reading only status_changes[-1] loses them."""
    _stub_polls(
        monkeypatch,
        [
            _changes(
                "joining_call",
                "in_call_not_recording",
                (
                    "recording_permission_denied",
                    "zoom_local_recording_request_disabled",
                ),
                "recording_permission_allowed",
                "in_call_recording",
            )
        ],
    )

    status = recall._wait_until_bot_ready("bot-3", timeout_seconds=10)

    assert status == "in_call_recording"


def test_wait_until_ready_still_aborts_on_call_ended(monkeypatch):
    "Genuinely terminal states must keep short-circuiting the wait."
    _stub_polls(
        monkeypatch,
        [_changes("joining_call", "in_call_not_recording", "call_ended")],
    )

    status = recall._wait_until_bot_ready("bot-4", timeout_seconds=10)

    assert status == "call_ended"


def test_start_recording_does_not_post_on_real_denial(monkeypatch):
    "A real denial must abort before issuing start_recording."
    _stub_polls(
        monkeypatch,
        [
            _changes(
                "joining_call",
                ("recording_permission_denied", "host_denied_recording_request"),
            )
        ],
    )

    def fail_client(**_):
        raise AssertionError("start_recording must not be called after a denial")

    monkeypatch.setattr(httpx, "Client", fail_client)

    assert recall.start_recording("bot-5") == {}
