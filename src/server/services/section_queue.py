"""Request-scoped queue for streaming section_ready events from report_finalize."""

from __future__ import annotations

import asyncio


class SectionQueue:
    """One instance per request. report_finalize pushes; orchestrator drains."""

    SENTINEL = object()
    _SENTINEL = SENTINEL  # backwards-compatible alias

    def __init__(self) -> None:
        self._q: asyncio.Queue = asyncio.Queue()

    def push(self, section_id: str, content: str, source: str, title: str = "") -> None:
        self._q.put_nowait(
            {"id": section_id, "content": content, "source": source, "title": title}
        )

    def done(self) -> None:
        self._q.put_nowait(self.SENTINEL)

    async def wait_next(self):
        """Wait for next queued item (or SENTINEL when queue is done)."""
        return await self._q.get()

    async def __aiter__(self):
        while True:
            item = await self._q.get()
            if item is self.SENTINEL:
                return
            yield item

    def pending_count(self) -> int:
        return self._q.qsize()
