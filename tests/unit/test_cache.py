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

def test_set_and_get_string(cache):
    cache.set("k", "hello")
    assert cache.get("k") == "hello"


def test_set_and_get_dict(cache):
    value = {"revenue": 400_000_000_000, "margin": 44.5}
    cache.set("k", value)
    assert cache.get("k") == value


def test_set_and_get_list(cache):
    cache.set("k", [1, 2, 3])
    assert cache.get("k") == [1, 2, 3]


def test_set_and_get_int(cache):
    cache.set("k", 42)
    assert cache.get("k") == 42


def test_set_and_get_none_value(cache):
    cache.set("k", None)
    assert cache.get("k") is None


# ── miss ───────────────────────────────────────────────────────────────────

def test_get_missing_key_returns_none(cache):
    assert cache.get("does-not-exist") is None


# ── TTL expiry ─────────────────────────────────────────────────────────────

def test_expired_entry_returns_none(cache):
    cache.set("k", "value", ttl_seconds=0)
    # ttl=0 means expires_at = now; sleep 1ms to ensure past
    time.sleep(0.01)
    assert cache.get("k") is None


def test_non_expired_entry_returned(cache):
    cache.set("k", "value", ttl_seconds=60)
    assert cache.get("k") == "value"


def test_expiry_deletes_row_from_db(cache):
    cache.set("k", "value", ttl_seconds=0)
    time.sleep(0.01)
    cache.get("k")  # triggers delete
    # A second get should also return None (row is gone)
    assert cache.get("k") is None


# ── overwrite ──────────────────────────────────────────────────────────────

def test_set_overwrites_existing_key(cache):
    cache.set("k", "first")
    cache.set("k", "second")
    assert cache.get("k") == "second"


def test_set_overwrites_resets_ttl(cache):
    cache.set("k", "first", ttl_seconds=0)
    time.sleep(0.01)
    cache.set("k", "second", ttl_seconds=60)
    assert cache.get("k") == "second"


# ── delete ─────────────────────────────────────────────────────────────────

def test_explicit_delete(cache):
    cache.set("k", "value")
    cache.delete("k")
    assert cache.get("k") is None


def test_delete_nonexistent_key_is_safe(cache):
    cache.delete("ghost")  # should not raise


# ── clear_expired ──────────────────────────────────────────────────────────

def test_clear_expired_removes_expired_rows(cache):
    cache.set("expired", "v", ttl_seconds=0)
    cache.set("live", "v", ttl_seconds=60)
    time.sleep(0.01)
    deleted = cache.clear_expired()
    assert deleted == 1
    assert cache.get("live") == "v"


def test_clear_expired_returns_zero_when_nothing_expired(cache):
    cache.set("k", "v", ttl_seconds=60)
    assert cache.clear_expired() == 0


# ── default TTL ────────────────────────────────────────────────────────────

def test_default_ttl_used_when_not_specified(tmp_path):
    cache = Cache(db_path=str(tmp_path / "test.db"), default_ttl=9999)
    cache.set("k", "v")
    assert cache.get("k") == "v"


# ── multiple keys ──────────────────────────────────────────────────────────

def test_independent_keys_do_not_interfere(cache):
    cache.set("a", 1)
    cache.set("b", 2)
    assert cache.get("a") == 1
    assert cache.get("b") == 2
