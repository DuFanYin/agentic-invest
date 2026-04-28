"""Unit tests for LLMClient — all HTTP calls mocked."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.server.services.llm_provider import LLMClient


# ── helpers ────────────────────────────────────────────────────────────────

def _ok_response(content: str = '{"ok": true}', status: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = {
        "choices": [{"message": {"content": content}}]
    }
    resp.text = content
    return resp


def _error_response(status: int, body: dict | None = None) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = body or {"error": {"code": status, "message": "error"}}
    resp.text = json.dumps(body or {})
    return resp


def _client(model: str = "test/model") -> LLMClient:
    client = LLMClient(api_key="test-key", model=model)
    client.retry_backoff = 0.0
    return client


def _run(coro):
    return asyncio.run(coro)


# ── basic success ──────────────────────────────────────────────────────────


def test_complete_strips_markdown_fences():
    with patch("httpx.AsyncClient") as mock_http:
        mock_http.return_value.__aenter__.return_value.post.return_value = _ok_response(
            '```json\n{"ok": true}\n```'
        )
        result = _run(_client().complete("test prompt"))
    assert result == '{"ok": true}'


def test_complete_raises_without_api_key():
    with patch("src.server.services.llm_provider.LLM_API_KEY", None):
        client = LLMClient(model="test/model")
    with pytest.raises(RuntimeError, match="LLM API key is not set"):
        _run(client.complete("test prompt"))


# ── retry on 429 ──────────────────────────────────────────────────────────

def test_complete_retries_on_429_then_succeeds():
    post = AsyncMock(side_effect=[_error_response(429), _ok_response()])
    with patch("httpx.AsyncClient") as mock_http:
        mock_http.return_value.__aenter__.return_value.post = post
        result = _run(_client().complete("test"))
    assert result == '{"ok": true}'
    assert post.call_count == 2


def test_complete_falls_back_to_next_model_after_exhausted_retries():
    call_count = {"n": 0}
    def side_effect(*a, **kw):
        call_count["n"] += 1
        payload = kw.get("json") or {}
        if payload.get("model") == "model-a":
            return _error_response(429)
        return _ok_response()

    client = LLMClient(api_key="test-key")
    client._models = ["model-a", "model-b"]
    client.max_retries = 1
    client.retry_backoff = 0.0

    with patch("httpx.AsyncClient") as mock_http:
        mock_http.return_value.__aenter__.return_value.post.side_effect = side_effect
        result = _run(client.complete("test"))

    assert result == '{"ok": true}'


def test_complete_raises_when_all_models_exhausted():
    client = LLMClient(api_key="test-key")
    client._models = ["model-a"]
    client.max_retries = 1
    client.retry_backoff = 0.0

    with patch("httpx.AsyncClient") as mock_http:
        mock_http.return_value.__aenter__.return_value.post.return_value = _error_response(429)
        with pytest.raises(RuntimeError, match="All models exhausted"):
            _run(client.complete("test"))


# ── fatal errors skip immediately ─────────────────────────────────────────

def test_complete_skips_model_on_400():
    call_count = {"n": 0}
    def side_effect(*a, **kw):
        call_count["n"] += 1
        payload = kw.get("json") or {}
        if payload.get("model") == "model-a":
            return _error_response(400)
        return _ok_response()

    client = LLMClient(api_key="test-key")
    client._models = ["model-a", "model-b"]

    with patch("httpx.AsyncClient") as mock_http:
        mock_http.return_value.__aenter__.return_value.post.side_effect = side_effect
        result = _run(client.complete("test"))

    assert result == '{"ok": true}'


# ── call_with_retry JSON fail → simplified prompt ─────────────────────────

def test_call_with_retry_uses_simplified_prompt_on_json_fail():
    """First call returns invalid JSON; second call (simplified prompt) succeeds."""
    call_prompts: list[str] = []
    # Realistic prompt: long schema/rules preamble before the EVIDENCE section
    original_prompt = (
        "Return exactly this JSON schema:\n"
        "{ 'claims': [...], 'business_quality': {...} }\n"
        "Rules:\n- claim must cite evidence\n- confidence: high|medium|low\n\n"
        "EVIDENCE\n[ev_001] Revenue grew 22% YoY.\n[ev_002] Margins expanded 200bps."
    )

    async def fake_complete(prompt: str, *, system=None, node="unknown") -> str:
        call_prompts.append(prompt)
        if len(call_prompts) == 1:
            raise RuntimeError("invalid JSON from model: not-json")
        return '{"ok": true}'

    client = _client()
    client.complete = fake_complete  # type: ignore[method-assign]
    result = _run(client.call_with_retry(original_prompt))

    assert result == '{"ok": true}'
    assert len(call_prompts) == 2
    # Second prompt is stripped to just the data section + short preamble
    assert "Return only valid JSON" in call_prompts[1]
    assert len(call_prompts[1]) < len(call_prompts[0])


def test_call_with_retry_raises_when_simplified_prompt_also_fails():
    """Both attempts fail — RuntimeError propagates."""
    async def fake_complete(prompt: str, *, system=None, node="unknown") -> str:
        raise RuntimeError("invalid JSON from model: still broken")

    client = _client()
    client.complete = fake_complete  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="invalid JSON"):
        _run(client.call_with_retry("prompt"))


def test_call_with_retry_propagates_non_json_errors_immediately():
    """Transport/protocol failures bypass the simplified-prompt retry."""
    call_count = {"n": 0}

    async def fake_complete(prompt: str, *, system=None, node="unknown") -> str:
        call_count["n"] += 1
        raise RuntimeError("HTTP 503")

    client = _client()
    client.complete = fake_complete  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="HTTP 503"):
        _run(client.call_with_retry("prompt"))

    assert call_count["n"] == 1  # no retry attempted


# ── request payload shape ───────────────────────────────────────────────────

def test_complete_sends_expected_payload_shape():
    captured = {}

    def capture(*a, **kw):
        captured["payload"] = kw.get("json", {})
        return _ok_response()

    with patch("httpx.AsyncClient") as mock_http:
        mock_http.return_value.__aenter__.return_value.post.side_effect = capture
        _run(_client().complete("user msg", system="custom system"))

    messages = captured["payload"]["messages"]
    assert messages[0]["role"] == "system"
    assert messages[0]["content"] == "custom system"
    assert captured["payload"]["response_format"] == {"type": "json_object"}
