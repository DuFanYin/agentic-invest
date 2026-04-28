import json
import re
from datetime import UTC, datetime

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from src.server import shutdown
from src.server.agents.orchestrator import OrchestratorAgent
from src.server.models.request import ResearchRequest
from src.server.models.response import ResearchResponse
from src.server.utils.status import FAILED_PHASE_BY_AGENT, initial_agent_statuses

router = APIRouter()


@router.post("/research", response_model=ResearchResponse)
async def run_research(request: ResearchRequest) -> ResearchResponse:
    return await OrchestratorAgent().run(request)


@router.post("/research/stream")
def run_research_stream(request: ResearchRequest) -> StreamingResponse:
    async def event_stream():
        def emit(event: str, payload) -> str:
            data = json.dumps(payload, ensure_ascii=False)
            return f"event: {event}\ndata: {data}\n\n"

        orchestrator = OrchestratorAgent()

        def current_phase(statuses: list[dict], node: str | None) -> str:
            if node:
                for item in statuses:
                    if item.get("agent") == node and item.get("phase"):
                        return item["phase"]
            for item in statuses:
                if item.get("lifecycle") == "active" and item.get("phase"):
                    return item["phase"]
            return "unknown"

        # Emit initial statuses immediately — UI never blank after clicking Run
        boot_statuses = [
            s.model_dump() for s in initial_agent_statuses(running="parse_intent")
        ]
        last_statuses: list[dict] = boot_statuses
        yield emit("agent_status", boot_statuses)

        try:
            async for event in orchestrator.run_stream(request):
                if shutdown.is_set():
                    yield emit(
                        "error",
                        {
                            "timestamp": datetime.now(UTC).isoformat(),
                            "message": "research stream interrupted: server shutting down",
                            "node": "shutdown",
                            "phase": "shutdown",
                        },
                    )
                    yield emit("done", {})
                    return

                payload = event["payload"]
                if hasattr(payload, "model_dump"):
                    payload = payload.model_dump()

                event_type = event["type"]

                if event_type == "agent_status" and isinstance(payload, list):
                    last_statuses = payload

                yield emit(event_type, payload)

        except Exception as exc:
            message = str(exc) or "research stream failed"
            m = re.match(r"^\[([a-z_]+)\]\s+", message.strip())
            if m:
                node = m.group(1)
            else:
                msg = message.lower()
                if "scenario_scoring" in msg:
                    node = "scenario_scoring"
                elif "report_finalize" in msg:
                    node = "report_finalize"
                else:
                    node = None
            statuses = last_statuses or [
                s.model_dump() for s in initial_agent_statuses()
            ]
            now = datetime.now(UTC).isoformat()
            next_statuses = []
            for item in statuses:
                item = dict(item)
                item["last_update_at"] = now
                if node and item.get("agent") == node:
                    item["lifecycle"] = "failed"
                    item["phase"] = FAILED_PHASE_BY_AGENT.get(node, "idle")
                    item["action"] = "node failed"
                    item["last_error"] = message
                elif not node and item.get("agent") == "parse_intent":
                    item["lifecycle"] = "failed"
                    item["phase"] = "workflow_complete"
                    item["action"] = "workflow failed"
                    item["last_error"] = message
                next_statuses.append(item)
            statuses = next_statuses
            phase = current_phase(statuses, node)
            yield emit("agent_status", statuses)
            yield emit(
                "error",
                {
                    "timestamp": datetime.now(UTC).isoformat(),
                    "message": message,
                    "node": node or "unknown",
                    "phase": phase,
                },
            )

        yield emit("done", {})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )
