"""
OpenRouter LLM client.

Strategy
────────
- Primary model: openai/gpt-oss-20b:free  (fast, reliable JSON)
- Fallback chain: openai/gpt-oss-120b:free → nvidia/nemotron-3-super-120b-a12b:free
- Per-model: up to 2 retries with 2 s exponential backoff on 429 / 5xx
- response_format json_object enforced on every call
- Strips accidental markdown fences before JSON parsing
- Loads .env automatically if OPENROUTER_API_KEY not already in environment
"""

from __future__ import annotations

import json
import logging
import os
import re
import time

import httpx

logger = logging.getLogger(__name__)

# Ordered list: first is primary, rest are fallbacks tried in sequence.
_FREE_MODELS = [
    "openai/gpt-oss-20b:free",
    "openai/gpt-oss-120b:free",
    "nvidia/nemotron-3-super-120b-a12b:free",
]

_RETRYABLE_CODES = {429, 500, 502, 503, 504}


def _load_env() -> None:
    """
    Load .env from the repo root if the key is not already set.
    Walks up from this file until it finds a .env or hits the filesystem root.
    """
    if os.getenv("OPENROUTER_API_KEY"):
        return
    try:
        from dotenv import load_dotenv  # type: ignore[import]
        directory = os.path.dirname(os.path.abspath(__file__))
        for _ in range(8):  # max 8 levels up
            candidate = os.path.join(directory, ".env")
            if os.path.isfile(candidate):
                load_dotenv(candidate)
                return
            parent = os.path.dirname(directory)
            if parent == directory:
                break
            directory = parent
    except ImportError:
        pass


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
        base_url: str = "https://openrouter.ai/api/v1",
        timeout_seconds: float = 30.0,
        max_retries: int = 2,
        retry_backoff: float = 2.0,
    ) -> None:
        _load_env()
        self.api_key = api_key or os.getenv("OPENROUTER_API_KEY")
        # If a specific model is requested (e.g. from .env), use only that one.
        # Otherwise use the full fallback chain.
        env_model = os.getenv("OPENROUTER_MODEL")
        if model:
            self._models = [model]
        elif env_model and env_model not in _FREE_MODELS:
            # User explicitly configured a paid/custom model — respect it.
            self._models = [env_model]
        else:
            self._models = _FREE_MODELS
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.retry_backoff = retry_backoff

    # ── public API ─────────────────────────────────────────────────────────

    def complete(self, prompt: str, *, system: str | None = None) -> str:
        """
        Send a prompt and return the raw JSON string from the model.

        Raises RuntimeError if all models and retries are exhausted.
        The returned string is guaranteed to be valid JSON.
        """
        if not self.api_key:
            raise RuntimeError(
                "OPENROUTER_API_KEY is not set. "
                "Add it to your .env file or export it as an environment variable."
            )

        last_error: Exception | None = None

        for model_id in self._models:
            for attempt in range(1, self.max_retries + 2):  # +2: attempt 1 + N retries
                try:
                    content = self._call(model_id, prompt, system=system)
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
                    # Non-retryable error from this model — skip to next immediately.
                    last_error = exc
                    logger.warning("model %s fatal error: %s — skipping", model_id, exc)
                    break

        raise RuntimeError(
            f"All models exhausted. Last error: {last_error}"
        ) from last_error

    def complete_json(self, prompt: str, *, system: str | None = None) -> dict:
        """Convenience wrapper that parses and returns the JSON dict directly."""
        raw = self.complete(prompt, system=system)
        return json.loads(raw)

    # ── internals ──────────────────────────────────────────────────────────

    def _call(self, model_id: str, prompt: str, *, system: str | None) -> str:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        if os.getenv("OPENROUTER_HTTP_REFERER"):
            headers["HTTP-Referer"] = os.getenv("OPENROUTER_HTTP_REFERER", "")
        if os.getenv("OPENROUTER_APP_TITLE"):
            headers["X-Title"] = os.getenv("OPENROUTER_APP_TITLE", "")

        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        else:
            messages.append({
                "role": "system",
                "content": "Return only valid JSON. Do not include markdown, comments, or extra text.",
            })
        messages.append({"role": "user", "content": prompt})

        payload: dict = {
            "model": model_id,
            "messages": messages,
            "temperature": 0,
            "response_format": {"type": "json_object"},
        }

        try:
            with httpx.Client(timeout=self.timeout_seconds) as client:
                response = client.post(
                    f"{self.base_url}/chat/completions",
                    headers=headers,
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

        # OpenRouter sometimes wraps errors in a 200 response body.
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

        content = _strip_fences(content)

        try:
            json.loads(content)
        except json.JSONDecodeError as exc:
            # Not valid JSON — treat as retryable (model may just be flaky).
            raise _RetryableError(f"invalid JSON from model: {content[:120]}") from exc

        return content


# ── internal exception types ───────────────────────────────────────────────

class _RetryableError(Exception):
    """Retry this model."""


class _FatalError(Exception):
    """Skip to next model immediately."""
