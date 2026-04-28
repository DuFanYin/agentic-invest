"""Unit tests for Cache — uses in-memory SQLite (:memory: not supported via path,
so we use a temp file per test via tmp_path fixture)."""

from __future__ import annotations

import time

import pytest

from src.server.services.cache import Cache


@pytest.fixture
def cache(tmp_path):
    return Cache(db_path=str(tmp_path / "test.db"))


# ── set / get roundtrip ────────────────────────────────────────────────────

def test_set_and_get_roundtrip(cache):
    """Covers serialization for multiple value types."""
    for key, value in [("s", "hello"), ("d", {"x": 1}), ("l", [1, 2]), ("i", 42), ("n", None)]:
        cache.set(key, value)
        assert cache.get(key) == value


# ── TTL expiry ─────────────────────────────────────────────────────────────

def test_expired_entry_returns_none(cache):
    cache.set("k", "value", ttl_seconds=0)
    time.sleep(0.01)
    assert cache.get("k") is None


# ── overwrite ──────────────────────────────────────────────────────────────

def test_set_overwrites_existing_key_and_ttl(cache):
    cache.set("k", "first")
    cache.set("k", "second")
    assert cache.get("k") == "second"
    cache.set("k", "first", ttl_seconds=0)
    time.sleep(0.01)
    cache.set("k", "second", ttl_seconds=60)
    assert cache.get("k") == "second"


# ── delete ─────────────────────────────────────────────────────────────────

def test_explicit_delete(cache):
    cache.set("k", "value")
    cache.delete("k")
    assert cache.get("k") is None


# ── clear_expired ──────────────────────────────────────────────────────────

def test_clear_expired_removes_only_expired_rows(cache):
    cache.set("expired", "v", ttl_seconds=0)
    cache.set("live", "v", ttl_seconds=60)
    time.sleep(0.01)
    deleted = cache.clear_expired()
    assert deleted == 1
    assert cache.get("live") == "v"


# ── default TTL and key isolation ─────────────────────────────────────────

def test_default_ttl_used_when_not_specified(tmp_path):
    cache = Cache(db_path=str(tmp_path / "test.db"), default_ttl=9999)
    cache.set("k", "v")
    assert cache.get("k") == "v"
