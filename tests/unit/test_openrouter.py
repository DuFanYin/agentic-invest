"""Unit tests for OpenRouterClient — all HTTP calls mocked."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.server.services.openrouter import OpenRouterClient, _strip_fences


# ── _strip_fences ──────────────────────────────────────────────────────────

def test_strip_fences_removes_json_fence():
    assert _strip_fences('```json\n{"ok": true}\n```') == '{"ok": true}'


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


def _client(model: str = "test/model") -> OpenRouterClient:
    return OpenRouterClient(api_key="test-key", model=model)


def _run(coro):
    return asyncio.run(coro)


# ── basic success ──────────────────────────────────────────────────────────

def test_complete_json_returns_parsed_dict():
    with patch("httpx.AsyncClient") as mock_http:
        mock_http.return_value.__aenter__.return_value.post.return_value = _ok_response()
        result = _run(_client().complete_json("test prompt"))
    assert result == {"ok": True}


def test_complete_strips_markdown_fences():
    with patch("httpx.AsyncClient") as mock_http:
        mock_http.return_value.__aenter__.return_value.post.return_value = _ok_response(
            '```json\n{"ok": true}\n```'
        )
        result = _run(_client().complete("test prompt"))
    assert result == '{"ok": true}'


def test_complete_raises_without_api_key():
    with patch("src.server.services.openrouter.LLM_API_KEY", None):
        client = OpenRouterClient(model="test/model")
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

    client = OpenRouterClient(api_key="test-key")
    client._models = ["model-a", "model-b"]
    client.max_retries = 1

    with patch("httpx.AsyncClient") as mock_http:
        mock_http.return_value.__aenter__.return_value.post.side_effect = side_effect
        result = _run(client.complete("test"))

    assert result == '{"ok": true}'


def test_complete_raises_when_all_models_exhausted():
    client = OpenRouterClient(api_key="test-key")
    client._models = ["model-a"]
    client.max_retries = 1

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

    client = OpenRouterClient(api_key="test-key")
    client._models = ["model-a", "model-b"]

    with patch("httpx.AsyncClient") as mock_http:
        mock_http.return_value.__aenter__.return_value.post.side_effect = side_effect
        result = _run(client.complete("test"))

    assert result == '{"ok": true}'
    assert call_count["n"] == 2


# ── invalid JSON treated as retryable ─────────────────────────────────────

def test_complete_retries_on_invalid_json():
    post = AsyncMock(side_effect=[_ok_response("not json at all"), _ok_response('{"ok": true}')])
    with patch("httpx.AsyncClient") as mock_http:
        mock_http.return_value.__aenter__.return_value.post = post
        result = _run(_client().complete("test"))
    assert result == '{"ok": true}'


# ── system prompt and response_format ─────────────────────────────────────

def test_custom_system_prompt_sent():
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


def test_response_format_json_object_in_payload():
    captured = {}
    def capture(*a, **kw):
        captured["payload"] = kw.get("json", {})
        return _ok_response()

    with patch("httpx.AsyncClient") as mock_http:
        mock_http.return_value.__aenter__.return_value.post.side_effect = capture
        _run(_client().complete("test"))

    assert captured["payload"]["response_format"] == {"type": "json_object"}
