"""Macro capability — fetches FRED + market signals and assembles Evidence items."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from src.server.models.evidence import Evidence
from src.server.services.macro_data import MacroDataClient

logger = logging.getLogger(__name__)


@dataclass
class MacroFetchResult:
    evidence: list[Evidence]
    next_ev_id: int


async def fetch_macro_evidence(
    *,
    ev_id_start: int,
    retrieved_at: str,
    client: MacroDataClient,
) -> MacroFetchResult:
    evidence: list[Evidence] = []
    ev_id = ev_id_start

    try:
        macro_all = await client.get_all()
        fred = macro_all.get("fred", {})
        signals = macro_all.get("market_signals", {})

        if fred:
            fred_lines = []
            for series_id, item in fred.items():
                val = item.get("value")
                if val is None:
                    continue
                fred_lines.append(
                    f"{item.get('label', series_id)}: {val} ({item.get('direction', 'stable')})"
                )
            if fred_lines:
                evidence.append(Evidence(
                    id=f"ev_{ev_id:03d}",
                    source_type="macro_api",
                    title="FRED Economic Indicators",
                    url="https://fred.stlouisfed.org",
                    retrieved_at=retrieved_at,
                    summary="Key economic indicators: " + "; ".join(fred_lines),
                    reliability="high",
                    related_topics=["macro", "interest_rates", "inflation", "gdp", "employment"],
                ))
                ev_id += 1

        if signals:
            sig_lines = []
            for ticker_sym, item in signals.items():
                val = item.get("value")
                if val is None:
                    continue
                sig_lines.append(
                    f"{item.get('label', ticker_sym)}: {val} ({item.get('direction', 'stable')})"
                )
            if sig_lines:
                evidence.append(Evidence(
                    id=f"ev_{ev_id:03d}",
                    source_type="macro_api",
                    title="Macro Market Signals",
                    url="https://finance.yahoo.com",
                    retrieved_at=retrieved_at,
                    summary="Market signals: " + "; ".join(sig_lines),
                    reliability="high",
                    related_topics=["macro", "vix", "rates", "dollar"],
                ))
                ev_id += 1
    except Exception:
        logger.warning("macro data collection failed", exc_info=True)

    return MacroFetchResult(evidence=evidence, next_ev_id=ev_id)
