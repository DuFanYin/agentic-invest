import json
from datetime import UTC, datetime

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from src.server.agents.orchestrator import OrchestratorAgent
from src.server.models.request import ResearchRequest
from src.server.models.response import ResearchResponse

router = APIRouter()

_orchestrator = OrchestratorAgent()


@router.post("/research", response_model=ResearchResponse)
def run_research(request: ResearchRequest) -> ResearchResponse:
    return _orchestrator.run(request)


@router.post("/research/stream")
def run_research_stream(request: ResearchRequest) -> StreamingResponse:
    def event_stream():
        for event in _orchestrator.run_stream(request):
            payload = event["payload"]
            if hasattr(payload, "model_dump"):
                payload = payload.model_dump()
            timestamp = datetime.now(UTC).isoformat()
            data = json.dumps(payload, ensure_ascii=False)
            yield f"event: {event['type']}\ndata: {data}\n\n"
            timeline_data = json.dumps(
                {"timestamp": timestamp, "event": event["type"], "payload": payload},
                ensure_ascii=False,
            )
            yield f"event: timeline\ndata: {timeline_data}\n\n"
        yield "event: done\ndata: {}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )
