"""
OpenAI-compatible LLM client.

Strategy
────────
- Provider: openrouter (default) or openai (from env)
- Default model set depends on provider; can be overridden only in code
- Per-model: up to 2 retries with 2 s exponential backoff on 429 / 5xx
- complete()       — enforces response_format json_object; validates JSON before returning
- complete_text()  — free-form text (Markdown reports); skips JSON mode and validation
- call_with_retry() — agent-level retry wrapper over complete()

Telemetry
─────────
Pass a LLMCallCollector at construction time to capture call events.
The client itself holds no mutable call state — isolation is the collector's job.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from src.server import shutdown

import httpx

from src.server.models.response import LLMCall
from src.server.config import (
    LLM_API_KEY,
    LLM_APP_TITLE,
    LLM_BASE_URL,
    LLM_HTTP_REFERER,
    LLM_PROVIDER,
)
from src.server.utils.status import AGENT_TAG_BY_NODE

if TYPE_CHECKING:
    from src.server.services.collector import LLMCallCollector

logger = logging.getLogger(__name__)

_FREE_MODELS = [
    "openai/gpt-oss-120b:free",
    "qwen/qwen3-next-80b-a3b-instruct:free",
    "meta-llama/llama-3.3-70b-instruct:free",
    "google/gemma-3-27b-it:free",
]
_OPENAI_MODELS = [
    "gpt-4.1",
]

_RETRYABLE_CODES = {429, 500, 502, 503, 504}
def _strip_fences(text: str) -> str:
    """Remove ```json ... ``` or ``` ... ``` wrapping that some models emit."""
    text = text.strip()
    match = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
    return match.group(1).strip() if match else text


class OpenRouterClient:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str = LLM_BASE_URL,
        timeout_seconds: float = 30.0,
        max_retries: int = 2,
        retry_backoff: float = 2.0,
        collector: LLMCallCollector | None = None,
    ) -> None:
        self.provider = LLM_PROVIDER if LLM_PROVIDER in {"openrouter", "openai"} else "openrouter"
        self.api_key = api_key or LLM_API_KEY
        self._models = [model] if model else self._default_models()
        resolved_base_url = (LLM_BASE_URL or base_url).rstrip("/")
        self.base_url = resolved_base_url
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.retry_backoff = retry_backoff
        self._collector = collector  # None → telemetry disabled

    # ── public API ─────────────────────────────────────────────────────────

    async def complete(self, prompt: str, *, system: str | None = None, node: str = "unknown") -> str:
        """Send prompt, return validated JSON string. Raises RuntimeError on exhaustion."""
        return await self._run(prompt, system=system, json_mode=True, node=node)

    async def complete_json(self, prompt: str, *, system: str | None = None, node: str = "unknown") -> dict:
        """Convenience wrapper — parse and return the JSON dict directly."""
        return json.loads(await self.complete(prompt, system=system, node=node))

    async def complete_text(self, prompt: str, *, system: str | None = None, node: str = "unknown") -> str:
        """Send prompt, return raw text (no JSON enforcement). For Markdown reports."""
        return await self._run(prompt, system=system, json_mode=False, node=node)

    async def call_with_retry(
        self,
        prompt: str,
        *,
        system: str | None = None,
        attempts: int = 2,
        node: str = "unknown",
    ) -> str:
        """
        Call complete() up to `attempts` times, swallowing per-attempt errors.
        Raises RuntimeError only after all attempts fail.
        """
        last_exc: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                return await self.complete(prompt, system=system, node=node)
            except Exception as exc:
                last_exc = exc
                logger.warning("call_with_retry attempt %d/%d failed: %s", attempt, attempts, exc)
        raise RuntimeError(f"LLM call failed after {attempts} attempts") from last_exc

    # ── internals ──────────────────────────────────────────────────────────

    def _headers(self) -> dict:
        h = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        if self.provider == "openrouter":
            if LLM_HTTP_REFERER:
                h["HTTP-Referer"] = LLM_HTTP_REFERER
            if LLM_APP_TITLE:
                h["X-Title"] = LLM_APP_TITLE
        return h

    def _default_models(self) -> list[str]:
        if self.provider == "openai":
            return list(_OPENAI_MODELS)
        return list(_FREE_MODELS)

    def _emit(self, call: LLMCall) -> None:
        if self._collector is not None:
            self._collector.record(call)

    def _next_id(self) -> str:
        if self._collector is not None:
            return self._collector.next_id()
        return "llm_notrace"

    async def _run(self, prompt: str, *, system: str | None, json_mode: bool, node: str) -> str:
        if not self.api_key:
            raise RuntimeError(
                "LLM API key is not set. "
                "Set LLM_API_KEY in your environment."
            )

        last_error: Exception | None = None

        for model_id in self._models:
            for attempt in range(1, self.max_retries + 2):
                if shutdown.is_set():
                    raise RuntimeError("server shutting down")

                call_id = self._next_id()
                started_at = datetime.now(UTC).isoformat()

                self._emit(LLMCall(
                    id=call_id, node=node,
                    agent_tag=AGENT_TAG_BY_NODE.get(node, "?"),
                    model=model_id, attempt=attempt,
                    status="calling", started_at=started_at,
                ))

                try:
                    content = await self._call(model_id, prompt, system=system, json_mode=json_mode)
                    finished_at = datetime.now(UTC).isoformat()
                    self._emit(LLMCall(
                        id=call_id, node=node,
                        agent_tag=AGENT_TAG_BY_NODE.get(node, "?"),
                        model=model_id, attempt=attempt,
                        status="success",
                        latency_ms=_latency_ms(started_at, finished_at),
                        started_at=started_at, finished_at=finished_at,
                    ))
                    return content

                except _RetryableError as exc:
                    last_error = exc
                    finished_at = datetime.now(UTC).isoformat()
                    self._emit(LLMCall(
                        id=call_id, node=node,
                        agent_tag=AGENT_TAG_BY_NODE.get(node, "?"),
                        model=model_id, attempt=attempt,
                        status="retry",
                        latency_ms=_latency_ms(started_at, finished_at),
                        started_at=started_at, finished_at=finished_at,
                        error=str(exc)[:200],
                    ))
                    if attempt <= self.max_retries:
                        wait = self.retry_backoff ** attempt
                        logger.warning(
                            "model %s attempt %d/%d failed (%s) — retrying in %.1fs",
                            model_id, attempt, self.max_retries + 1, exc, wait,
                        )
                        # Interruptible backoff that can wake up early on shutdown.
                        if await shutdown.wait_or_timeout(wait):
                            raise RuntimeError("server shutting down")
                    else:
                        logger.warning("model %s exhausted retries, trying next", model_id)

                except _FatalError as exc:
                    last_error = exc
                    finished_at = datetime.now(UTC).isoformat()
                    self._emit(LLMCall(
                        id=call_id, node=node,
                        agent_tag=AGENT_TAG_BY_NODE.get(node, "?"),
                        model=model_id, attempt=attempt,
                        status="failed",
                        latency_ms=_latency_ms(started_at, finished_at),
                        started_at=started_at, finished_at=finished_at,
                        error=str(exc)[:200],
                    ))
                    logger.warning("model %s fatal error: %s — skipping", model_id, exc)
                    break

        raise RuntimeError(f"All models exhausted. Last error: {last_error}") from last_error

    async def _call(self, model_id: str, prompt: str, *, system: str | None, json_mode: bool) -> str:
        default_system = (
            "Return only valid JSON. Do not include markdown, comments, or extra text."
            if json_mode else None
        )
        messages = []
        if system or default_system:
            messages.append({"role": "system", "content": system or default_system})
        messages.append({"role": "user", "content": prompt})

        payload: dict = {"model": model_id, "messages": messages, "temperature": 0, "max_tokens": 2048}
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers=self._headers(),
                    json=payload,
                )
        except httpx.TimeoutException as exc:
            raise _RetryableError(f"timeout: {exc}") from exc
        except httpx.NetworkError as exc:
            raise _RetryableError(f"network error: {exc}") from exc

        if response.status_code in _RETRYABLE_CODES:
            raise _RetryableError(f"HTTP {response.status_code}")
        if response.status_code >= 400:
            raise _FatalError(f"HTTP {response.status_code}: {response.text[:200]}")

        data = response.json()
        if "error" in data and "choices" not in data:
            code = data["error"].get("code", 0)
            msg = data["error"].get("message", "unknown")
            if code in _RETRYABLE_CODES or code == 0:
                raise _RetryableError(f"API error {code}: {msg}")
            raise _FatalError(f"API error {code}: {msg}")

        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise _FatalError("response missing message content") from exc

        if isinstance(content, list):
            content = "".join(
                c.get("text", "") if isinstance(c, dict) else str(c) for c in content
            )

        if not isinstance(content, str) or not content.strip():
            raise _FatalError("model returned empty content")

        content = content.strip()

        if json_mode:
            content = _strip_fences(content)
            try:
                json.loads(content)
            except json.JSONDecodeError as exc:
                raise _RetryableError(f"invalid JSON from model: {content[:120]}") from exc

        return content


# ── helpers ────────────────────────────────────────────────────────────────

def _latency_ms(started_at: str, finished_at: str) -> int:
    try:
        delta = (
            datetime.fromisoformat(finished_at.replace("Z", "+00:00"))
            - datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        ).total_seconds()
        return max(0, int(delta * 1000))
    except Exception:
        return 0


# ── internal exception types ───────────────────────────────────────────────

class _RetryableError(Exception):
    """Retry this model."""


class _FatalError(Exception):
    """Skip to next model immediately."""
