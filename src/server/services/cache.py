"""
SQLite-backed TTL cache.

Key = caller-supplied string (typically sha256 of the query).
Values are JSON-serialised. Expired rows are deleted on read.
Thread-safe via SQLite's WAL mode + a per-instance lock.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path


class Cache:
    def __init__(self, db_path: str = "cache.db", default_ttl: int = 3600) -> None:
        self._db_path = db_path
        self._default_ttl = default_ttl
        self._lock = threading.Lock()
        self._init_db()

    # ── public API ─────────────────────────────────────────────────────────

    def get(self, key: str) -> object | None:
        """Return the cached value, or None on miss or expiry."""
        with self._lock:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT value, expires_at FROM cache WHERE key = ?", (key,)
                ).fetchone()
            if row is None:
                return None
            value_json, expires_at = row
            if time.time() > expires_at:
                self._delete(key)
                return None
            try:
                return json.loads(value_json)
            except json.JSONDecodeError:
                # Corrupted cache row should degrade to miss, not break callers.
                self._delete(key)
                return None

    def set(self, key: str, value: object, ttl_seconds: int | None = None) -> None:
        """Store value under key, expiring after ttl_seconds (default: default_ttl)."""
        ttl = ttl_seconds if ttl_seconds is not None else self._default_ttl
        expires_at = time.time() + ttl
        value_json = json.dumps(value)
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO cache (key, value, expires_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(key) DO UPDATE SET value = excluded.value,
                                                   expires_at = excluded.expires_at
                    """,
                    (key, value_json, expires_at),
                )

    def delete(self, key: str) -> None:
        """Explicitly remove a key."""
        with self._lock:
            self._delete(key)

    def clear_expired(self) -> int:
        """Delete all expired rows. Returns count deleted."""
        with self._lock:
            with self._connect() as conn:
                cursor = conn.execute(
                    "DELETE FROM cache WHERE expires_at <= ?", (time.time(),)
                )
                return cursor.rowcount

    # ── internals ──────────────────────────────────────────────────────────

    def _init_db(self) -> None:
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS cache (
                    key        TEXT PRIMARY KEY,
                    value      TEXT NOT NULL,
                    expires_at REAL NOT NULL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_expires ON cache (expires_at)")

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._db_path)

    def _delete(self, key: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM cache WHERE key = ?", (key,))
