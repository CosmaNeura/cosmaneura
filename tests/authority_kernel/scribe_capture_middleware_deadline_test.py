"""Final network-send ownership through Core's real middleware graph, offline."""

import asyncio
import json
import time
from threading import BoundedSemaphore, get_ident

import pytest
from fastapi import APIRouter, FastAPI, HTTPException, Request

from app.routes import middleware
from app.routes import scribe_capture_deadline as capture
from app.services.scribe_capture_deadline import check_capture_deadline

PREFIX = "/service/v1/agent/scribe/"
PATHS = tuple(
    PREFIX + suffix
    for suffix in (
        "capture-bindings/resolve",
        "capture-admissions",
        "capture-operations",
        "capture-notes",
        "encounter-context",
        "native-capture",
    )
)
BUDGET = 0.08
GRACE = 0.04


def make_app(monkeypatch, behavior="success"):
    monkeypatch.setattr(capture, "CAPTURE_REQUEST_SECONDS", BUDGET)
    monkeypatch.setattr(capture, "ERROR_SEND_SECONDS", GRACE)
    monkeypatch.setattr(capture, "_workers", BoundedSemaphore(4))
    monkeypatch.setattr(middleware, "ALLOWED_HOST_LIST", "testserver")
    target = FastAPI()
    router = APIRouter(route_class=capture.CaptureDeadlineRoute)

    async def endpoint():
        if behavior == "denied":
            raise HTTPException(403, "synthetic denial")
        if behavior == "unexpected":
            raise RuntimeError("never disclose private source")
        if behavior == "timeout":
            await asyncio.sleep(60)
        return {"content": "synthetic private output"}

    for path in PATHS:
        router.add_api_route(path, endpoint, methods=["POST"])
    target.include_router(router)
    target.add_api_route("/unrelated", endpoint, methods=["GET"])
    middleware.add_middleware(target)
    capture.install_capture_boundaries(target)
    return target


def scope(path, method="POST"):
    return {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [(b"host", b"testserver")],
        "server": ("127.0.0.1", 1),
        "client": ("127.0.0.1", 2),
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("path", [PATHS[0]])
@pytest.mark.parametrize("behavior", ["success", "denied", "unexpected", "timeout"])
@pytest.mark.parametrize("stalled_type", ["http.response.start", "http.response.body"])
async def test_actual_middleware_final_send_is_bounded(
    monkeypatch, path, behavior, stalled_type
):
    target = make_app(monkeypatch, behavior)
    blocked = asyncio.Event()
    messages = []

    async def receive():
        await blocked.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        messages.append(message.copy())
        if message["type"] == stalled_type:
            await blocked.wait()

    started = time.monotonic()
    task = asyncio.create_task(target(scope(path), receive, send))
    done, _ = await asyncio.wait({task}, timeout=0.4)
    try:
        assert done, "full middleware escaped the finite public response budget"
        task.result()
        assert time.monotonic() - started < 0.35
        assert messages
        assert len([m for m in messages if m["type"] == "http.response.start"]) == 1
        assert not any(b"never disclose" in m.get("body", b"") for m in messages)
        if stalled_type == "http.response.start":
            assert len(messages) == 1, (
                "no subsequent private or static body after timeout"
            )
    finally:
        blocked.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("path", [PATHS[0]])
async def test_cancellation_of_final_send_admits_no_late_body(monkeypatch, path):
    target = make_app(monkeypatch)
    entered, release = asyncio.Event(), asyncio.Event()
    messages = []

    async def receive():
        await asyncio.Event().wait()
        return {"type": "http.disconnect"}

    async def send(message):
        messages.append(message.copy())
        entered.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            await release.wait()  # Deliberately resistant transport cleanup.

    task = asyncio.create_task(target(scope(path), receive, send))
    await asyncio.wait_for(entered.wait(), 1)
    task.cancel()
    done, _ = await asyncio.wait({task}, timeout=0.2)
    try:
        assert done and task.cancelled()
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
    await asyncio.sleep(0.02)
    assert [m["type"] for m in messages] == ["http.response.start"]


@pytest.mark.asyncio
@pytest.mark.parametrize("path", [*PATHS, "/unrelated"])
@pytest.mark.parametrize("behavior,status", [("success", 200), ("denied", 403)])
async def test_known_output_and_headers_survive_actual_middleware(
    monkeypatch, path, behavior, status
):
    target = make_app(monkeypatch, behavior)
    messages = []

    async def receive():
        await asyncio.Event().wait()
        return {"type": "http.disconnect"}

    async def send(message):
        messages.append(message)

    await target(scope(path, "GET" if path == "/unrelated" else "POST"), receive, send)
    assert messages[0]["status"] == status
    assert b"no-store" in dict(messages[0]["headers"])[b"cache-control"]
    body = b"".join(m.get("body", b"") for m in messages)
    assert json.loads(body) == (
        {"content": "synthetic private output"}
        if status == 200
        else {"detail": "synthetic denial"}
    )


def test_main_application_boundaries_are_outermost():
    from app.server import api_app

    assert api_app.user_middleware[0].cls is capture.CaptureBoundary
    assert set(api_app.state.capture_deadline_paths) == set(PATHS)


@pytest.mark.asyncio
async def test_one_private_capture_worker_owns_full_middleware(monkeypatch):
    target = make_app(monkeypatch)
    observed = []
    parent = get_ident()

    @target.middleware("http")
    async def added_before_boundary(request: Request, call_next):
        observed.append((get_ident(), check_capture_deadline()))
        return await call_next(request)

    # A new outer application ensures our deliberately added middleware is also
    # inside the boundary. This uses the production boundary, not a mock sender.
    wrapped = capture.CaptureBoundary(target, paths=frozenset(PATHS))
    messages = []

    async def receive():
        await asyncio.Event().wait()
        return {"type": "http.disconnect"}

    async def send(message):
        messages.append(message)

    await wrapped(scope(PATHS[0]), receive, send)
    assert messages[0]["status"] == 200
    assert len(observed) == 1
    assert observed[0][0] != parent and observed[0][1] is not None


def test_installation_rejects_route_inventory_drift(monkeypatch):
    installer = capture.install_capture_boundaries
    with pytest.raises(ValueError, match="capture_route_inventory"):
        installer(FastAPI())
    target = make_app(monkeypatch)
    with pytest.raises(ValueError, match="capture_boundary_already_installed"):
        installer(target)


@pytest.mark.asyncio
async def test_worker_start_failure_uses_bounded_static_output(monkeypatch):
    target = make_app(monkeypatch)

    def unavailable(_self):
        raise RuntimeError("private thread infrastructure detail")

    monkeypatch.setattr(capture.Thread, "start", unavailable)
    messages = []

    async def receive():
        pytest.fail("failed worker must not read the request")

    async def send(message):
        messages.append(message)
        await asyncio.Event().wait()

    task = asyncio.create_task(target(scope(PATHS[0]), receive, send))
    done, _ = await asyncio.wait({task}, timeout=0.2)
    try:
        assert done
        task.result()
        assert [message["status"] for message in messages] == [503]
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path,method,status",
    [
        (PATHS[0], "GET", 405),
        ("/unrelated", "GET", 200),
    ],
)
async def test_unselected_requests_do_not_borrow_capture_capacity(
    monkeypatch, path, method, status
):
    target = make_app(monkeypatch)
    monkeypatch.setattr(capture, "_workers", BoundedSemaphore(0))
    messages = []

    async def receive():
        await asyncio.Event().wait()
        return {"type": "http.disconnect"}

    async def send(message):
        messages.append(message)

    await target(scope(path, method), receive, send)
    assert messages[0]["status"] == status
