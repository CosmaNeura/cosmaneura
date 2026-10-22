"""Bounded admission for trusted provider work; not a private OS threadpool."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from weakref import WeakKeyDictionary

from anyio import CapacityLimiter, fail_after

from app.services.provider_database_deadline import ProviderDatabaseDeadlineExceeded

ADMISSION_SECONDS = 1.0
ACTIVE_SLOTS = 2


@dataclass
class Lane:
    slots: CapacityLimiter
    dispatch: CapacityLimiter


_lanes: WeakKeyDictionary[asyncio.AbstractEventLoop, Lane] = WeakKeyDictionary()


def _lane() -> Lane:
    loop = asyncio.get_running_loop()
    lane = _lanes.get(loop)
    if lane is None:
        # Manual reservations bound active work. The dispatch limiter avoids
        # acquiring another slot or waiting on AnyIO's shared default limiter.
        lane = Lane(CapacityLimiter(ACTIVE_SLOTS), CapacityLimiter(float("inf")))
        _lanes[loop] = lane
    return lane


@asynccontextmanager
async def provider_thread_slot() -> AsyncIterator[CapacityLimiter]:
    lane = _lane()
    try:
        # Only acquisition is timed and remains cancellable. Once work starts,
        # its retained task must finish cleanup before this slot is released.
        with fail_after(ADMISSION_SECONDS):
            await lane.slots.acquire()
    except TimeoutError:
        raise ProviderDatabaseDeadlineExceeded from None
    try:
        yield lane.dispatch
    finally:
        lane.slots.release()


def _after_fork() -> None:
    global _lanes
    _lanes = WeakKeyDictionary()


os.register_at_fork(after_in_child=_after_fork)
