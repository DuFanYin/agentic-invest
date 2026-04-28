"""Normalize capability — conflict detection and NormalizedData assembly.

Pure function: no I/O, no shared state access.
"""

from __future__ import annotations

from src.server.models.analysis import Conflict, MetricsBlock, NormalizedData
from src.server.models.evidence import Evidence
from src.server.models.intent import ResearchIntent


def detect_conflicts(evidence: list[Evidence]) -> list[dict]:
    """Detect factual conflicts across evidence items.

    Flags topics covered by both high and low reliability sources from
    different source types — a signal that downstream agents should prefer
    the high-reliability source.
    """
    conflicts: list[dict] = []
    topic_sources: dict[str, list[Evidence]] = {}
    for ev in evidence:
        for topic in ev.related_topics:
            topic_sources.setdefault(topic, []).append(ev)

    for topic, items in topic_sources.items():
        source_types = {ev.source_type for ev in items}
        reliabilities = {ev.reliability for ev in items}
        if "high" in reliabilities and "low" in reliabilities and len(source_types) > 1:
            conflicts.append({
                "topic": topic,
                "type": "reliability_divergence",
                "evidence_ids": [ev.id for ev in items],
                "note": (
                    f"Topic '{topic}' covered by both high and low-reliability sources "
                    f"({', '.join(sorted(source_types))}). Downstream agents should "
                    f"prefer high-reliability evidence."
                ),
            })
    return conflicts


def normalize_evidence(
    query: str,
    intent: ResearchIntent | None,
    evidence: list[Evidence],
    metrics: dict,
    missing_fields: list[str],
    retry_questions: list[str],
    pass_id: int,
) -> NormalizedData:
    raw_conflicts = detect_conflicts(evidence)
    conflicts = [Conflict.model_validate(c) for c in raw_conflicts]
    metrics_block = MetricsBlock(
        ttm=metrics.get("ttm", {}),
        three_year_avg=metrics.get("three_year_avg", {}),
        latest_quarter=metrics.get("latest_quarter", {}),
        price_history=metrics.get("price_history", {}),
    )
    return NormalizedData(
        query=query,
        intent=intent.model_dump() if intent else {},
        metrics=metrics_block,
        missing_fields=missing_fields,
        conflicts=conflicts,
        open_question_context=retry_questions,
        pass_id=pass_id,
    )
