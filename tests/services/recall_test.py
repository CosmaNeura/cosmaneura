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

    monkeypatch.setattr(httpx, "Client", lambda: FakeClient())

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
            raise httpx.HTTPStatusError("", request=None, response=self)

    class FakeClient:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def post(self, url, headers=None):
            return FakeResponse()

    monkeypatch.setattr(httpx, "Client", lambda: FakeClient())

    # Should NOT raise — 400 means bot already terminated
    recall.leave_call("bot-abc-123")


def test_create_bot_forwards_metadata(monkeypatch):
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

    monkeypatch.setattr(httpx, "Client", lambda: FakeClient())

    recall.create_bot(
        meeting_url="https://zoom.us/j/123",
        zak_token="zak",
        metadata={"doctor_id": "doctor_x", "meeting_id": "m1"},
    )

    assert captured["payload"]["metadata"] == {
        "doctor_id": "doctor_x",
        "meeting_id": "m1",
    }
