"""Synthetic official Bot-response contract; no provider, DB, audio or consent claim."""

import asyncio
from copy import deepcopy

import httpx
import pytest

from app.services import recall

BOT = "264ad8b0-2a69-4a57-9e77-0aebd1192c98"
RECORDING = "41b2c9b7-2662-4d8b-a566-710985c7a741"
OLD = "d7066c49-4c4e-4795-a4eb-2e59c48b08f5"
AT = "2026-10-25T10:00:00Z"


def recording(key=RECORDING, *, started: str | None = AT, completed=None):
    return {
        "id": key,
        "created_at": AT,
        "started_at": started,
        "completed_at": completed,
        "status": {"code": "processing", "sub_code": None, "updated_at": AT},
        "media_shortcuts": None,
    }


def bot(rows=(), status="in_call_not_recording", key=BOT):
    # Top-level id identifies the Bot. Each recording is a separate UUID.
    return {
        "id": key,
        "meeting_url": {"platform": "zoom", "meeting_id": "12345678901"},
        "calendar_meetings": [],
        "recordings": list(rows),
        "status_changes": [
            {"code": status, "created_at": AT, "message": "", "sub_code": ""}
        ],
    }


def transport(monkeypatch, after, before=None, polls=()):
    calls = {"posts": [], "reads": []}
    queue = iter([before if before is not None else bot(), *polls])

    def read(key, timeout):
        calls["reads"].append((key, timeout))
        value = next(queue)
        if isinstance(value, Exception):
            raise value
        return deepcopy(value)

    class Client:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def post(self, url, **kwargs):
            calls["posts"].append((url, kwargs))
            if isinstance(after, Exception):
                raise after
            return httpx.Response(200, json=after, request=httpx.Request("POST", url))

    monkeypatch.setattr(recall, "_read_recording_bot", read)
    monkeypatch.setattr(recall.httpx, "Client", lambda **_: Client())
    return calls


def test_official_bot_response_yields_new_recording_not_bot_id(monkeypatch):
    calls = transport(monkeypatch, bot([recording()], "in_call_recording"))
    assert recall.start_recording(BOT, wait_for_ready=False) == {"id": RECORDING}
    assert len(calls["posts"]) == 1
    assert calls["posts"][0][1]["json"] == {"retention": {"type": "timed", "hours": 6}}


def test_delayed_new_recording_and_current_status_are_observed_without_second_post(
    monkeypatch,
):
    calls = transport(
        monkeypatch,
        bot(),
        polls=[bot([recording(started=None)]), bot([recording()], "in_call_recording")],
    )
    monkeypatch.setattr(recall.time, "sleep", lambda _: None)
    assert recall.start_recording(BOT, wait_for_ready=False) == {"id": RECORDING}
    assert len(calls["posts"]) == 1
    assert len(calls["reads"]) == 3
    assert all(key == BOT and 0 < timeout <= 5 for key, timeout in calls["reads"])


def test_optional_start_fields_wait_for_explicit_current_recording(monkeypatch):
    pending = recording()
    del pending["started_at"]
    del pending["completed_at"]
    calls = transport(
        monkeypatch,
        bot([pending], "in_call_recording"),
        polls=[bot([recording()], "in_call_recording")],
    )
    monkeypatch.setattr(recall.time, "sleep", lambda _: None)
    assert recall.start_recording(BOT, wait_for_ready=False) == {"id": RECORDING}
    assert len(calls["posts"]) == 1


def test_old_completed_recording_is_never_selected(monkeypatch):
    old = recording(OLD, completed=AT)
    calls = transport(
        monkeypatch,
        bot([old]),
        before=bot([old]),
        polls=[bot([old, recording()], "in_call_recording")],
    )
    monkeypatch.setattr(recall.time, "sleep", lambda _: None)
    assert recall.start_recording(BOT, wait_for_ready=False) == {"id": RECORDING}
    assert len(calls["posts"]) == 1


@pytest.mark.parametrize(
    "before",
    [
        bot([recording()]),
        bot(status="in_call_recording"),
        bot(key="foreign-bot"),
        bot([recording()], "done"),
    ],
)
def test_unconfirmed_or_existing_recording_before_start_prevents_post(
    monkeypatch, before
):
    calls = transport(
        monkeypatch, bot([recording()], "in_call_recording"), before=before
    )
    with pytest.raises(ValueError):
        recall.start_recording(BOT, wait_for_ready=False)
    assert calls["posts"] == []


@pytest.mark.parametrize(
    "after",
    [
        bot([recording()], "in_call_recording", key="foreign-bot"),
        bot([recording(BOT)], "in_call_recording"),
        bot([recording(), recording()], "in_call_recording"),
        bot([recording(), recording(OLD)], "in_call_recording"),
        bot([recording(completed=AT)], "in_call_recording"),
        bot([recording(started="not-a-timestamp")], "in_call_recording"),
        bot([recording()], "fatal"),
        {"id": RECORDING},
    ],
)
def test_ambiguous_invalid_or_terminal_post_response_raises_without_replay(
    monkeypatch, after
):
    calls = transport(monkeypatch, after)
    with pytest.raises(ValueError):
        recall.start_recording(BOT, wait_for_ready=False)
    assert len(calls["posts"]) == 1


def test_post_timeout_retains_uncertainty_and_is_never_repeated(monkeypatch):
    calls = transport(monkeypatch, httpx.ReadTimeout("synthetic timeout"))
    with pytest.raises(httpx.ReadTimeout):
        recall.start_recording(BOT, wait_for_ready=False)
    assert len(calls["posts"]) == 1


def test_post_start_polling_has_one_total_deadline_and_no_replay(monkeypatch):
    calls = transport(monkeypatch, bot(), polls=[bot()] * 40)
    clock = [0.0]
    monkeypatch.setattr(recall.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        recall.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds)
    )
    with pytest.raises(TimeoutError, match="recall_recording_start_unconfirmed"):
        recall.start_recording(BOT, wait_for_ready=False)
    assert clock[0] == 15.0
    assert len(calls["posts"]) == 1
    assert calls["reads"][-1][1] == 0.5


def test_ready_already_recording_raises_before_any_post(monkeypatch):
    monkeypatch.setattr(recall, "_wait_until_bot_ready", lambda _: "in_call_recording")
    calls = transport(monkeypatch, {})
    with pytest.raises(ValueError, match="recall_recording_already_active"):
        recall.start_recording(BOT)
    assert calls == {"posts": [], "reads": []}


def test_whole_read_timeout_bounds_a_slow_header_response(monkeypatch):
    original = httpx.AsyncClient

    async def delayed(_):
        await asyncio.sleep(1)
        return httpx.Response(200, json=bot())

    monkeypatch.setattr(
        recall.httpx,
        "AsyncClient",
        lambda **kwargs: original(transport=httpx.MockTransport(delayed), **kwargs),
    )
    with pytest.raises(TimeoutError):
        recall._read_recording_bot(BOT, 0.01)


def test_whole_read_timeout_also_bounds_a_slow_streamed_body(monkeypatch):
    original = httpx.AsyncClient

    class SlowBody(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"{"
            await asyncio.sleep(1)
            yield b"}"

    monkeypatch.setattr(
        recall.httpx,
        "AsyncClient",
        lambda **kwargs: original(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(200, stream=SlowBody())
            ),
            **kwargs,
        ),
    )
    with pytest.raises(TimeoutError):
        recall._read_recording_bot(BOT, 0.01)


def test_poll_body_size_is_bounded_without_following_redirects(monkeypatch):
    original = httpx.AsyncClient
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, content=b"x" * 1048577)

    monkeypatch.setattr(
        recall.httpx,
        "AsyncClient",
        lambda **kwargs: original(transport=httpx.MockTransport(respond), **kwargs),
    )
    with pytest.raises(ValueError, match="response_too_large"):
        recall._read_recording_bot(BOT, 1)
    assert len(requests) == 1
    assert requests[0].url == httpx.URL(
        f"{recall.RECALL_API_BASE.rstrip('/')}/bot/{BOT}/"
    )


def test_recording_get_never_follows_a_redirect(monkeypatch):
    original = httpx.AsyncClient
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(302, headers={"location": "https://elsewhere.invalid/"})

    monkeypatch.setattr(
        recall.httpx,
        "AsyncClient",
        lambda **kwargs: original(transport=httpx.MockTransport(respond), **kwargs),
    )
    with pytest.raises(httpx.HTTPStatusError):
        recall._read_recording_bot(BOT, 1)
    assert len(requests) == 1


@pytest.mark.parametrize("path", ["/bot/", f"/bot/{BOT}/start_recording/"])
@pytest.mark.parametrize("phase", ["headers", "body"])
def test_whole_mutation_timeout_cancels_one_request_without_background_retry(
    monkeypatch, path, phase
):
    original = httpx.AsyncClient
    requests, cancellations = [], []

    class SlowBody(httpx.AsyncByteStream):
        async def __aiter__(self):
            try:
                yield b"{"
                await asyncio.sleep(10)
                yield b"}"
            finally:
                cancellations.append("body")

    async def respond(request):
        requests.append(request)
        if phase == "headers":
            try:
                await asyncio.sleep(10)
            finally:
                cancellations.append("headers")
        return httpx.Response(200, stream=SlowBody())

    monkeypatch.setattr(
        recall.httpx,
        "AsyncClient",
        lambda **kwargs: original(transport=httpx.MockTransport(respond), **kwargs),
    )
    with pytest.raises(TimeoutError):
        recall._bounded_provider_json("POST", path, recall.time.monotonic() + 0.02, {})
    assert len(requests) == 1 and cancellations == [phase]


def test_exhausted_budget_starts_no_request(monkeypatch):
    monkeypatch.setattr(
        recall.httpx, "AsyncClient", lambda **_: pytest.fail("request after deadline")
    )
    with pytest.raises(TimeoutError):
        recall._bounded_provider_json("POST", "/bot/", recall.time.monotonic() - 1, {})


def test_admitted_launch_preserves_full_ready_window_and_one_post(monkeypatch):
    clock, calls = [0.0], []
    monkeypatch.setattr(recall.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        recall.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds)
    )

    def request(method, path, deadline, payload=None, **kwargs):
        calls.append((method, deadline))
        if method == "POST":
            return bot([recording()], "in_call_recording")
        if clock[0] < 44:
            return bot(status="joining_call")
        return bot(status="recording_permission_allowed")

    monkeypatch.setattr(recall, "_bounded_provider_json", request)
    assert recall.start_recording(BOT, deadline=100) == {"id": RECORDING}
    assert clock[0] == 44 and sum(method == "POST" for method, _ in calls) == 1
    assert calls[0][1] == 45


def test_readiness_timeout_has_whole_45_second_budget_and_never_posts(monkeypatch):
    clock, calls = [0.0], []
    monkeypatch.setattr(recall.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        recall.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds)
    )

    def request(method, path, deadline, **kwargs):
        calls.append(method)
        clock[0] = deadline
        raise TimeoutError("whole response expired")

    monkeypatch.setattr(recall, "_bounded_provider_json", request)
    assert recall.start_recording(BOT, deadline=100) == {}
    assert clock[0] == 45 and calls == ["GET"]


def test_shared_confirmation_deadline_is_not_restarted(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(recall.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        recall.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds)
    )
    monkeypatch.setattr(recall, "_read_recording_bot", lambda *_: bot())
    with pytest.raises(TimeoutError):
        recall._confirm_new_recording(bot(), BOT, set(), deadline=1)
    assert clock[0] == 1


@pytest.mark.parametrize("code", ["paused", "done", "failed", "deleted"])
def test_nonprocessing_recording_is_not_current_capture(code):
    row = recording()
    row["status"]["code"] = code
    with pytest.raises(ValueError, match="not_processing"):
        recall._new_started_recording(bot([row], "in_call_recording"), BOT, set())
