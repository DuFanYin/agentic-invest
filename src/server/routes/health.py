from fastapi import APIRouter, status
from fastapi.responses import JSONResponse
from src.server.config import LLM_API_KEY, LLM_PROVIDER

router = APIRouter()


@router.get("/health")
def health_check() -> JSONResponse:
    ready = bool(LLM_API_KEY)
    payload = {
        "status": "ok" if ready else "degraded",
        "ready": ready,
        "provider": LLM_PROVIDER,
        "checks": {"llm_api_key": "ok" if ready else "missing"},
    }
    code = status.HTTP_200_OK if ready else status.HTTP_503_SERVICE_UNAVAILABLE
    return JSONResponse(status_code=code, content=payload)
