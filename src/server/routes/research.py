import json
import re
from datetime import UTC, datetime

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from src.server.agents.orchestrator import OrchestratorAgent
from src.server.models.request import ResearchRequest
from src.server.models.response import ResearchResponse
from src.server.utils.status import AGENT_NAMES

router = APIRouter()

def _new_orchestrator() -> OrchestratorAgent:
    return OrchestratorAgent()

_FAILED_PHASE_BY_AGENT = {
    "parse_intent": "planning",
    "research": "collecting_evidence",
    "fundamental_analysis": "analyzing_fundamentals",
    "market_sentiment": "analyzing_sentiment",
    "gap_check": "evaluating_gaps",
    "scenario_scoring": "scoring_scenarios",
    "report_verification": "generating_report",
}


def _node_from_error(message: str) -> str | None:
    m = re.match(r"^\[([a-z_]+)\]\s+", message.strip())
    if m:
        return m.group(1)
    msg = message.lower()
    if "scenario_scoring" in msg:
        return "scenario_scoring"
    if "report_verification" in msg:
        return "report_verification"
    return None


def _fallback_statuses(message: str) -> list[dict]:
    now = datetime.now(UTC).isoformat()
    statuses = []
    for agent in AGENT_NAMES:
        statuses.append(
            {
                "agent": agent,
                "lifecycle": "standby",
                "phase": "idle",
                "action": "waiting",
                "details": [],
                "entered_at": now,
                "last_update_at": now,
                "waiting_on": None,
                "progress_hint": None,
                "retry_count": 0,
                "max_retries": 0,
                "last_error": None,
            }
        )
    node = _node_from_error(message)
    for status in statuses:
        if node and status["agent"] == node:
            status["lifecycle"] = "failed"
            status["phase"] = _FAILED_PHASE_BY_AGENT.get(node, "idle")
            status["action"] = "node failed"
            status["last_error"] = message
        elif not node and status["agent"] == "parse_intent":
            # Only blame parse_intent when we can't identify the failing node
            status["lifecycle"] = "failed"
            status["phase"] = "workflow_complete"
            status["action"] = "workflow failed"
            status["last_error"] = message
    return statuses


@router.post("/research", response_model=ResearchResponse)
def run_research(request: ResearchRequest) -> ResearchResponse:
    return _new_orchestrator().run(request)


@router.post("/research/stream")
def run_research_stream(request: ResearchRequest) -> StreamingResponse:
    def event_stream():
        orchestrator = _new_orchestrator()
        last_agent_statuses: list[dict] = []
        try:
            for event in orchestrator.run_stream(request):
                payload = event["payload"]
                if hasattr(payload, "model_dump"):
                    payload = payload.model_dump()
                if event["type"] == "agent_status" and isinstance(payload, list):
                    last_agent_statuses = payload
                timestamp = datetime.now(UTC).isoformat()
                data = json.dumps(payload, ensure_ascii=False)
                yield f"event: {event['type']}\ndata: {data}\n\n"
                timeline_data = json.dumps(
                    {"timestamp": timestamp, "event": event["type"], "payload": payload},
                    ensure_ascii=False,
                )
                yield f"event: timeline\ndata: {timeline_data}\n\n"
        except Exception as exc:
            timestamp = datetime.now(UTC).isoformat()
            message = str(exc) or "research stream failed"
            statuses = last_agent_statuses or _fallback_statuses(message)
            node = _node_from_error(message)
            if node:
                now = datetime.now(UTC).isoformat()
                for item in statuses:
                    if item.get("agent") == node:
                        item["lifecycle"] = "failed"
                        item["phase"] = _FAILED_PHASE_BY_AGENT.get(node, "idle")
                        item["action"] = "node failed"
                        item["last_error"] = message
                        item["last_update_at"] = now
                    if item.get("agent") == "parse_intent":
                        item["lifecycle"] = "failed"
                        item["phase"] = "workflow_complete"
                        item["action"] = "workflow failed"
                        item["last_error"] = message
                        item["last_update_at"] = now
            status_data = json.dumps(statuses, ensure_ascii=False)
            yield f"event: agent_status\ndata: {status_data}\n\n"
            yield (
                "event: error\ndata: "
                + json.dumps(
                    {"timestamp": timestamp, "message": message, "node": node or "unknown"},
                    ensure_ascii=False,
                )
                + "\n\n"
            )
            yield (
                "event: timeline\ndata: "
                + json.dumps(
                    {
                        "timestamp": timestamp,
                        "event": "error",
                        "payload": {"message": message, "node": node or "unknown"},
                    },
                    ensure_ascii=False,
                )
                + "\n\n"
            )
        yield "event: done\ndata: {}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )
