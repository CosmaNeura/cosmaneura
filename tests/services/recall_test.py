import httpx

from app.services import recall


def test_leave_call_posts_to_bot_endpoint(monkeypatch):
    captured = {}

    class FakeResponse:
        status_code = 200
        text = '{"ok": true}'
        @property
        def is_success(self): return True
        def raise_for_status(self): return None

    class FakeClient:
        def __enter__(self): return self
        def __exit__(self, *a): return False
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
        def is_success(self): return False
        def raise_for_status(self):
            req = httpx.Request("POST", "http://test/")
            raise httpx.HTTPStatusError(
                "", request=req, response=httpx.Response(400, request=req)
            )

    class FakeClient:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def post(self, url, headers=None):
            return FakeResponse()

    monkeypatch.setattr(httpx, "Client", lambda **_: FakeClient())

    # Should NOT raise — 400 means bot already terminated
    recall.leave_call("bot-abc-123")


def test_create_bot_forwards_metadata_and_zak_url(monkeypatch):
    captured = {}

    class FakeResponse:
        status_code = 200
        text = '{"id": "bot-xyz"}'
        @property
        def is_success(self): return True
        def raise_for_status(self): return None
        def json(self): return {"id": "bot-xyz"}

    class FakeClient:
        def __enter__(self): return self
        def __exit__(self, *a): return False
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
    zoom_cfg = payload["zoom"]
    assert zoom_cfg["zak_url"] == (
        "https://app.cosmaneura.com/external/v1/recall/zak/abc"
    )
    assert zoom_cfg["require_recording_permission"] is True
    assert zoom_cfg["request_recording_permission_on_host_join"] is True
    assert "zak_token" not in zoom_cfg
