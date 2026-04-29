"""Process-wide shutdown flag — set on SIGINT/SIGTERM to drain active SSE streams."""

from __future__ import annotations

import asyncio
import threading

event = threading.Event()
async_event: asyncio.Event | None = None
enabled = False


def init_async_event() -> None:
    global async_event, enabled
    async_event = asyncio.Event()
    enabled = True
    event.clear()


def clear() -> None:
    event.clear()
    if async_event is not None:
        async_event.clear()


def set() -> None:
    event.set()
    if async_event is not None:
        async_event.set()


def is_set() -> bool:
    # Only active while server lifecycle has enabled shutdown signaling.
    if not enabled:
        return False
    return (async_event.is_set() if async_event is not None else False) or event.is_set()


async def wait_or_timeout(timeout: float) -> bool:
    """Return True if shutdown was signaled before timeout, else False."""
    if not enabled:
        await asyncio.sleep(timeout)
        return False
    if is_set():
        return True
    if async_event is not None:
        try:
            await asyncio.wait_for(async_event.wait(), timeout=timeout)
            return True
        except TimeoutError:
            return False
    return await asyncio.to_thread(event.wait, timeout)


def disable() -> None:
    """Disable shutdown signaling outside of active server lifecycle."""
    global async_event, enabled
    enabled = False
    async_event = None
