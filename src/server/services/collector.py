"""Request-scoped LLM call collector.

One instance is created per HTTP request by OrchestratorAgent.
OpenRouterClient writes events here; the orchestrator reads them.
No global state — concurrent requests are fully isolated.
"""

from __future__ import annotations

import threading

from src.server.models.response import LLMCall


class LLMCallCollector:
    def __init__(self) -> None:
        self._calls: list[LLMCall] = []
        self._lock = threading.Lock()
        self._seq = 0

    def next_id(self) -> str:
        with self._lock:
            self._seq += 1
            return f"llm_{self._seq:03d}"

    def record(self, call: LLMCall) -> None:
        with self._lock:
            self._calls.append(call)

    def drain(self) -> list[LLMCall]:
        """Return and clear all accumulated calls since last drain."""
        with self._lock:
            items = list(self._calls)
            self._calls.clear()
            return items

    def all(self) -> list[LLMCall]:
        """Return all calls without clearing (for final response assembly)."""
        with self._lock:
            return list(self._calls)
