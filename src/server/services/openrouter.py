"""
OpenRouter LLM client.

Strategy
────────
- Primary model: openai/gpt-oss-20b:free  (fast, reliable JSON)
- Fallback chain: openai/gpt-oss-120b:free → nvidia/nemotron-3-super-120b-a12b:free
- Per-model: up to 2 retries with 2 s exponential backoff on 429 / 5xx
- complete()      — enforces response_format json_object; validates JSON before returning
- complete_text() — free-form text (Markdown reports); skips JSON mode and validation
- call_with_retry() — agent-level retry wrapper over complete()
"""

from __future__ import annotations

import json
import logging
import re
import time

import httpx

from src.server.config import (
    OPENROUTER_API_KEY,
    OPENROUTER_APP_TITLE,
    OPENROUTER_BASE_URL,
    OPENROUTER_HTTP_REFERER,
    OPENROUTER_MODEL,
)

logger = logging.getLogger(__name__)

_FREE_MODELS = [
    "openai/gpt-oss-20b:free",
    "openai/gpt-oss-120b:free",
    "nvidia/nemotron-3-super-120b-a12b:free",
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
        base_url: str = OPENROUTER_BASE_URL,
        timeout_seconds: float = 30.0,
        max_retries: int = 2,
        retry_backoff: float = 2.0,
    ) -> None:
        self.api_key = api_key or OPENROUTER_API_KEY
        if model:
            self._models = [model]
        elif OPENROUTER_MODEL and OPENROUTER_MODEL not in _FREE_MODELS:
            self._models = [OPENROUTER_MODEL]
        else:
            self._models = _FREE_MODELS
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.retry_backoff = retry_backoff

    # ── public API ─────────────────────────────────────────────────────────

    def complete(self, prompt: str, *, system: str | None = None) -> str:
        """Send prompt, return validated JSON string. Raises RuntimeError on exhaustion."""
        return self._run(prompt, system=system, json_mode=True)

    def complete_json(self, prompt: str, *, system: str | None = None) -> dict:
        """Convenience wrapper — parse and return the JSON dict directly."""
        return json.loads(self.complete(prompt, system=system))

    def complete_text(self, prompt: str, *, system: str | None = None) -> str:
        """Send prompt, return raw text (no JSON enforcement). For Markdown reports."""
        return self._run(prompt, system=system, json_mode=False)

    def call_with_retry(
        self,
        prompt: str,
        *,
        system: str | None = None,
        attempts: int = 2,
    ) -> str:
        """
        Call complete() up to `attempts` times, swallowing per-attempt errors.
        Raises RuntimeError only after all attempts fail.
        Agents use this instead of rolling their own retry loop.
        """
        last_exc: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                return self.complete(prompt, system=system)
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
        if OPENROUTER_HTTP_REFERER:
            h["HTTP-Referer"] = OPENROUTER_HTTP_REFERER
        if OPENROUTER_APP_TITLE:
            h["X-Title"] = OPENROUTER_APP_TITLE
        return h

    def _run(self, prompt: str, *, system: str | None, json_mode: bool) -> str:
        if not self.api_key:
            raise RuntimeError(
                "OPENROUTER_API_KEY is not set. "
                "Add it to your .env file or export it as an environment variable."
            )

        last_error: Exception | None = None

        for model_id in self._models:
            for attempt in range(1, self.max_retries + 2):
                try:
                    content = self._call(model_id, prompt, system=system, json_mode=json_mode)
                    return content
                except _RetryableError as exc:
                    last_error = exc
                    if attempt <= self.max_retries:
                        wait = self.retry_backoff ** attempt
                        logger.warning(
                            "model %s attempt %d/%d failed (%s) — retrying in %.1fs",
                            model_id, attempt, self.max_retries + 1, exc, wait,
                        )
                        time.sleep(wait)
                    else:
                        logger.warning("model %s exhausted retries, trying next", model_id)
                except _FatalError as exc:
                    last_error = exc
                    logger.warning("model %s fatal error: %s — skipping", model_id, exc)
                    break

        raise RuntimeError(f"All models exhausted. Last error: {last_error}") from last_error

    def _call(self, model_id: str, prompt: str, *, system: str | None, json_mode: bool) -> str:
        default_system = (
            "Return only valid JSON. Do not include markdown, comments, or extra text."
            if json_mode else None
        )
        messages = []
        if system or default_system:
            messages.append({"role": "system", "content": system or default_system})
        messages.append({"role": "user", "content": prompt})

        payload: dict = {"model": model_id, "messages": messages, "temperature": 0}
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        try:
            with httpx.Client(timeout=self.timeout_seconds) as client:
                response = client.post(
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


# ── internal exception types ───────────────────────────────────────────────

class _RetryableError(Exception):
    """Retry this model."""


class _FatalError(Exception):
    """Skip to next model immediately."""
