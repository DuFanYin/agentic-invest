"""Web capability — Tavily search and assembles Evidence items."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from src.server.models.evidence import Evidence
from src.server.services.cache import Cache
from src.server.services.web_research import WebResearchClient

logger = logging.getLogger(__name__)

_WEB_TTL = 900  # 15 min


def _cache_key(prefix: str, *parts: str) -> str:
    import hashlib

    payload = ":".join(parts)
    return f"{prefix}:{hashlib.sha256(payload.encode()).hexdigest()[:16]}"


@dataclass
class WebFetchResult:
    evidence: list[Evidence]
    next_ev_id: int


async def _fetch_single(
    query: str,
    *,
    retrieved_at: str,
    seen_urls: set[str],
    cache: Cache,
    client: WebResearchClient,
) -> list[dict]:
    """Fetch one query's results; returns raw items (URL dedup applied in-place)."""
    try:
        ck = _cache_key("web", query)
        web_results = cache.get(ck)
        if web_results is None:
            web_results = await asyncio.to_thread(client.search, query, 5)
            cache.set(ck, web_results, ttl_seconds=_WEB_TTL)
        fresh = []
        for item in web_results:
            url = item.get("url", "")
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            fresh.append(item)
        return fresh
    except Exception:
        logger.warning("web search failed for query: %s", query, exc_info=True)
        return []


async def fetch_web_evidence(
    queries: str | list[str],
    *,
    ev_id_start: int,
    retrieved_at: str,
    seen_urls: set[str],
    cache: Cache,
    client: WebResearchClient,
) -> WebFetchResult:
    """Fetch web evidence for one or more queries concurrently.

    Accepts a single query string or a list. Multiple queries run in parallel;
    results are URL-deduplicated across all queries.
    """
    if isinstance(queries, str):
        queries = [queries]

    results = await asyncio.gather(
        *[
            _fetch_single(
                q,
                retrieved_at=retrieved_at,
                seen_urls=seen_urls,
                cache=cache,
                client=client,
            )
            for q in queries
        ]
    )

    evidence: list[Evidence] = []
    ev_id = ev_id_start
    for items in results:
        for item in items:
            evidence.append(
                Evidence(
                    id=f"ev_{ev_id:03d}",
                    source_type="web",
                    title=item.get("title", "Web result"),
                    url=item.get("url") or None,
                    published_at=item.get("published_date"),
                    retrieved_at=retrieved_at,
                    summary=item.get("content", item.get("title", "")),
                    reliability="medium",
                    related_topics=["web"],
                )
            )
            ev_id += 1

    return WebFetchResult(evidence=evidence, next_ev_id=ev_id)
