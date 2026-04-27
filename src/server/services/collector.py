"""Request-scoped LLM call collector.

One instance is created per HTTP request by OrchestratorAgent.
OpenRouterClient writes events here; the orchestrator reads them.
No global state — concurrent requests are fully isolated.
"""

from __future__ import annotations

import asyncio
import threading

from src.server.models.response import LLMCall


class LLMCallCollector:
    def __init__(self) -> None:
        self._calls: list[LLMCall] = []
        self._lock = threading.Lock()
        self._seq = 0
        self._pending: asyncio.Queue[LLMCall] = asyncio.Queue()

    def next_id(self) -> str:
        with self._lock:
            self._seq += 1
            return f"llm_{self._seq:03d}"

    def record(self, call: LLMCall) -> None:
        with self._lock:
            self._calls.append(call)
        self._pending.put_nowait(call)

    async def wait_next(self) -> LLMCall:
        """Wait for next emitted call event (for real-time stream push)."""
        return await self._pending.get()

    def pending_count(self) -> int:
        return self._pending.qsize()

    def all(self) -> list[LLMCall]:
        """Return all calls (for final response assembly)."""
        with self._lock:
            return list(self._calls)
