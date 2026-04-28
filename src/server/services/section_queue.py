"""Request-scoped queue for streaming section_ready events from report_finalize."""

from __future__ import annotations

import asyncio


class SectionQueue:
    """One instance per request. report_finalize pushes; orchestrator drains."""

    _SENTINEL = object()

    def __init__(self) -> None:
        self._q: asyncio.Queue = asyncio.Queue()

    def push(self, section_id: str, content: str, source: str) -> None:
        self._q.put_nowait({"id": section_id, "content": content, "source": source})

    def done(self) -> None:
        self._q.put_nowait(self._SENTINEL)

    async def __aiter__(self):
        while True:
            item = await self._q.get()
            if item is self._SENTINEL:
                return
            yield item

    def pending_count(self) -> int:
        return self._q.qsize()
