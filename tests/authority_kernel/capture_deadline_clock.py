"""Test-only capture clock advanced after explicit worker-entry barriers."""

import asyncio
import time
from types import SimpleNamespace

import pytest

from app.routes import scribe_capture_deadline as boundary
from app.services import scribe_capture_deadline as deadline_service


def coordinated_deadline(monkeypatch, *, seconds, control_send=False):
    """Control capture expiry without replacing global loops or clocks."""
    started = time.monotonic()
    now = started
    expired, waiting = asyncio.Event(), asyncio.Event()

    async def wait(futures, *, timeout=None, return_when=asyncio.ALL_COMPLETED):
        # Only the boundary's outcome/disconnect wait uses controlled expiry.
        # Public response sends keep the real timer unless explicitly controlled.
        futures = set(futures)
        if control_send and len(futures) == 1:
            # Identity-ownership tests hold simulated time during error delivery.
            # A real scheduler pause is not a simulated send-budget expiry.
            assert return_when == asyncio.ALL_COMPLETED
            assert timeout == pytest.approx(boundary.ERROR_SEND_SECONDS)
            return await asyncio.wait(futures, return_when=return_when)
        if len(futures) != 2 or return_when != asyncio.FIRST_COMPLETED:
            return await asyncio.wait(futures, timeout=timeout, return_when=return_when)
        assert timeout == pytest.approx(seconds)
        waiting.set()
        timer = asyncio.create_task(expired.wait())
        try:
            done, pending = await asyncio.wait(
                futures | {timer}, return_when=return_when
            )
            return done - {timer}, pending - {timer}
        finally:
            timer.cancel()
            await asyncio.gather(timer, return_exceptions=True)

    class AsyncioTimer:
        def __getattr__(self, name):
            return wait if name == "wait" else getattr(asyncio, name)

    def expire():
        nonlocal now
        assert now == started
        now = started + seconds
        expired.set()

    clock = SimpleNamespace(monotonic=lambda: now)
    monkeypatch.setattr(boundary, "time", clock)
    monkeypatch.setattr(deadline_service, "time", clock)
    monkeypatch.setattr(boundary, "asyncio", AsyncioTimer())
    return SimpleNamespace(expire=expire, waiting=waiting)
