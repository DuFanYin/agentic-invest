import json
import re
from datetime import UTC, datetime

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from src.server import shutdown
from src.server.agents.orchestrator import OrchestratorAgent
from src.server.models.request import ResearchRequest
from src.server.models.response import ResearchResponse
from src.server.utils.status import AGENT_NAMES, initial_agent_statuses

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


def _sse(event: str, payload) -> str:
    data = json.dumps(payload, ensure_ascii=False)
    return f"event: {event}\ndata: {data}\n\n"


def _mark_failed(statuses: list[dict], node: str | None, message: str) -> list[dict]:
    now = datetime.now(UTC).isoformat()
    result = []
    for item in statuses:
        item = dict(item)
        item["last_update_at"] = now
        if node and item.get("agent") == node:
            item["lifecycle"] = "failed"
            item["phase"] = _FAILED_PHASE_BY_AGENT.get(node, "idle")
            item["action"] = "node failed"
            item["last_error"] = message
        elif not node and item.get("agent") == "parse_intent":
            item["lifecycle"] = "failed"
            item["phase"] = "workflow_complete"
            item["action"] = "workflow failed"
            item["last_error"] = message
        result.append(item)
    return result


def _fallback_statuses(message: str) -> list[dict]:
    now = datetime.now(UTC).isoformat()
    return [
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
        for agent in AGENT_NAMES
    ]


@router.post("/research", response_model=ResearchResponse)
async def run_research(request: ResearchRequest) -> ResearchResponse:
    return await _new_orchestrator().run(request)


@router.post("/research/stream")
def run_research_stream(request: ResearchRequest) -> StreamingResponse:
    async def event_stream():
        orchestrator = _new_orchestrator()

        # Emit initial statuses immediately — UI never blank after clicking Run
        boot_statuses = [s.model_dump() for s in initial_agent_statuses(running="parse_intent")]
        last_statuses: list[dict] = boot_statuses
        yield _sse("agent_status", boot_statuses)

        try:
            async for event in orchestrator.run_stream(request):
                if shutdown.is_set():
                    yield _sse("done", {})
                    return

                payload = event["payload"]
                if hasattr(payload, "model_dump"):
                    payload = payload.model_dump()

                event_type = event["type"]

                if event_type == "agent_status" and isinstance(payload, list):
                    last_statuses = payload

                yield _sse(event_type, payload)

        except Exception as exc:
            message = str(exc) or "research stream failed"
            node = _node_from_error(message)
            statuses = _mark_failed(last_statuses or _fallback_statuses(message), node, message)
            yield _sse("agent_status", statuses)
            yield _sse("error", {
                "timestamp": datetime.now(UTC).isoformat(),
                "message": message,
                "node": node or "unknown",
            })

        yield _sse("done", {})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )
