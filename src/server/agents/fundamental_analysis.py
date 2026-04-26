from src.server.agents.research import ResearchResult


class FundamentalAnalysisAgent:
    def run(self, research_result: ResearchResult) -> dict:
        evidence_ids = [evidence.id for evidence in research_result.evidence]
        return {
            "agent": "fundamental_analysis",
            "claims": [
                {
                    "statement": "Core business quality remains resilient in the base case.",
                    "confidence": "medium",
                    "evidence_ids": evidence_ids[:2],
                },
                {
                    "statement": "Valuation is sensitive to growth deceleration assumptions.",
                    "confidence": "medium",
                    "evidence_ids": evidence_ids[1:],
                },
            ],
            "metrics": research_result.normalized_data.get("metrics", {}),
            "missing_fields": research_result.normalized_data.get("missing_fields", []),
            "business_quality": {
                "view": "stable",
                "drivers": ["competitive position", "execution quality"],
            },
            "financials": {
                "profitability_trend": "flat_to_improving",
                "cash_flow_quality": "moderate",
            },
            "valuation": {
                "relative_multiple_view": "near historical median",
                "simplified_dcf_view": "fair_value_range_wide",
            },
            "fundamental_risks": [
                {
                    "name": "Demand slowdown",
                    "impact": "medium",
                    "signal": "order growth below trailing average",
                    "evidence_ids": [evidence_ids[-1]] if evidence_ids else [],
                },
                {
                    "name": "Margin pressure",
                    "impact": "medium",
                    "signal": "gross margin trend down two consecutive quarters",
                    "evidence_ids": evidence_ids[:1],
                },
            ],
        }
