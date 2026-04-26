from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from src.server.routes.health import router as health_router
from src.server.routes.research import router as research_router


BASE_DIR = Path(__file__).resolve().parent
FRONTEND_DIR = BASE_DIR.parent / "frontend"

app = FastAPI(
    title="Investment Research Agent",
    description="A multi-agent investment research tool with scenario scoring.",
)

app.mount("/static", StaticFiles(directory=FRONTEND_DIR / "static"), name="static")
app.include_router(health_router)
app.include_router(research_router)


@app.get("/")
def index() -> FileResponse:
    return FileResponse(FRONTEND_DIR / "index.html")
