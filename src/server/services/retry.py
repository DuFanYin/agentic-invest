"""Shared retry/backoff helpers for external data fetchers."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")

logger = logging.getLogger(__name__)

RETRYABLE_HTTP_STATUS = {408, 429, 500, 502, 503, 504}
DEFAULT_FETCH_TIMEOUT_SECONDS = 15.0


class RetryableFetchError(RuntimeError):
    """Transient fetch failure that should be retried."""


def retry_sync(
    fn: Callable[[], T],
    *,
    attempts: int = 3,
    retry_on: tuple[type[Exception], ...] = (RetryableFetchError,),
    initial_backoff_seconds: float = 0.5,
    backoff_multiplier: float = 2.0,
    max_backoff_seconds: float = 4.0,
    op_name: str = "fetch",
) -> T:
    """Retry sync callable with exponential backoff on RetryableFetchError."""
    last_exc: Exception | None = None
    backoff = max(0.0, initial_backoff_seconds)
    total_attempts = max(1, attempts)

    for attempt in range(1, total_attempts + 1):
        try:
            return fn()
        except retry_on as exc:
            last_exc = exc
            if attempt >= total_attempts:
                break
            logger.warning(
                "%s transient failure (%d/%d): %s",
                op_name,
                attempt,
                total_attempts,
                exc,
            )
            if backoff > 0:
                time.sleep(backoff)
            backoff = min(
                max_backoff_seconds,
                backoff * backoff_multiplier if backoff > 0 else 0.0,
            )

    raise RuntimeError(
        f"{op_name} failed after {total_attempts} attempts"
    ) from last_exc
