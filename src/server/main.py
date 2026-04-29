from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

BASE_DIR = Path(__file__).resolve().parent
FRONTEND_DIR = BASE_DIR.parent / "frontend"
_REPO_ROOT = BASE_DIR.parent.parent


def _load_env() -> None:
    env_path = _REPO_ROOT / ".env"
    if not env_path.is_file():
        raise RuntimeError(
            f".env file not found at {env_path}. Create one from .env.example before starting the server."
        )
    try:
        from dotenv import load_dotenv  # type: ignore[import]
    except ImportError as exc:
        raise RuntimeError("python-dotenv is required but not installed") from exc
    load_dotenv(env_path)


_load_env()

from src.server import shutdown  # noqa: E402 — must come after env is loaded
from src.server.routes.health import router as health_router  # noqa: E402
from src.server.routes.research import router as research_router  # noqa: E402


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Ensure stale global shutdown flag never leaks across app lifecycles
    # (reload/tests may recreate the app in the same process).
    shutdown.init_async_event()
    shutdown.clear()
    try:
        yield
    finally:
        # uvicorn has already handled SIGINT/SIGTERM by this point — signal active SSE
        # generators and retry sleeps to abort immediately.
        shutdown.set()
        shutdown.disable()


app = FastAPI(
    title="Investment Research Agent",
    description="A multi-agent investment research tool with scenario scoring.",
    lifespan=lifespan,
)

app.mount("/static", StaticFiles(directory=FRONTEND_DIR / "static"), name="static")
app.include_router(health_router)
app.include_router(research_router)


@app.get("/")
def index() -> FileResponse:
    return FileResponse(FRONTEND_DIR / "index.html")
