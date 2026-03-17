"""
EPaperService — FastAPI entry point.

Owns the Waveshare 3.6inch e-Paper HAT+ (E) (600×400, 6-colour Spectra 6).
- Idle mode: refreshes stats at :00/:20/:40 of every hour.
- Agent mode: displays agent-pushed content; reverts to idle on expiry or /release.
"""

import asyncio
import io
import json
import logging
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .config import settings
from .routers.epaper import router as epaper_router
from .state import HistoryEntry, state

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
)
logger = logging.getLogger(__name__)

_STATIC  = Path(__file__).parent / "static"
DATA_DIR = Path(__file__).parent.parent / "data"
HIST_MAX = 50


# ── disk helpers ──────────────────────────────────────────────────────────────

def _hist_json() -> Path:
    return DATA_DIR / "history.json"


def _img_path(entry_id: str) -> Path:
    return DATA_DIR / f"{entry_id}.png"


def _save_history_index():
    _hist_json().write_text(
        json.dumps([
            {"id": e.id, "timestamp": e.timestamp,
             "mode": e.mode, "agent": e.agent, "description": e.description}
            for e in state.history
        ], indent=2)
    )


def _load_history_index():
    if not _hist_json().exists():
        return
    try:
        raw = json.loads(_hist_json().read_text())
        for r in raw:
            p = _img_path(r["id"])
            if p.exists():
                state.history.append(HistoryEntry(**r))
        logger.info("Loaded %d history entries from disk", len(state.history))
    except Exception as exc:
        logger.warning("Could not load history index: %s", exc)


# ── helpers ───────────────────────────────────────────────────────────────────

def next_refresh_ts() -> float:
    """Unix timestamp of the next :00/:20/:40 minute boundary."""
    now = datetime.now()
    interval = settings.idle_interval_min
    mark = (now.minute // interval + 1) * interval
    if mark >= 60:
        nxt = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    else:
        nxt = now.replace(minute=mark, second=0, microsecond=0)
    return nxt.timestamp()


def _record(img, mode: str, agent: str | None, description: str):
    """Save PIL image as PNG to disk and append a history entry."""
    from PIL import Image  # noqa

    entry_id = uuid.uuid4().hex[:12]
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    png = buf.getvalue()

    state.current_image_png = png
    _img_path(entry_id).write_bytes(png)

    entry = HistoryEntry(
        id=entry_id,
        timestamp=datetime.now().timestamp(),
        mode=mode,
        agent=agent,
        description=description,
    )
    state.history.append(entry)
    if len(state.history) > HIST_MAX:
        old = state.history.pop(0)
        _img_path(old.id).unlink(missing_ok=True)

    _save_history_index()


async def push_image(img, mode: str = "agent",
                     agent: str | None = None,
                     description: str = "agent message") -> None:
    """Push a PIL image to the display (non-blocking) and record to history."""
    from . import display
    _record(img, mode, agent, description)
    await asyncio.to_thread(display.show, img)


async def refresh_stats() -> None:
    """Render and push the idle stats screen."""
    from . import renderer
    logger.info("Refreshing stats display")
    img = await asyncio.to_thread(renderer.render_stats)
    await push_image(img, mode="stats", agent=None, description="stats refresh")
    state.last_updated = datetime.now().timestamp()


# ── idle loop ─────────────────────────────────────────────────────────────────

async def _idle_loop() -> None:
    await refresh_stats()
    while True:
        sleep_s = next_refresh_ts() - datetime.now().timestamp()
        await asyncio.sleep(max(sleep_s, 1))
        if state.is_expired():
            logger.info("Agent display expired, returning to idle")
            state.release()
        if state.mode == "idle":
            await refresh_stats()


# ── lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    _load_history_index()
    logger.info("EPaperService starting on %s:%d", settings.host, settings.port)
    task = asyncio.create_task(_idle_loop())
    yield
    task.cancel()
    logger.info("EPaperService shutting down.")


# ── app ───────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="EPaperService",
    description=(
        "Controls the Waveshare 3.6inch e-Paper HAT+ (E) display.\n\n"
        "Idle mode refreshes server stats every 20 minutes.\n\n"
        "Agents can call **POST /api/show** to take over the display."
    ),
    version="1.1.0",
    lifespan=lifespan,
)

app.include_router(epaper_router)

if _STATIC.exists():
    app.mount("/static", StaticFiles(directory=str(_STATIC)), name="static")

    @app.get("/", include_in_schema=False)
    async def admin_ui():
        return FileResponse(str(_STATIC / "admin.html"))


@app.get("/health", include_in_schema=False)
async def health():
    from .display import is_simulation
    return {
        "status": "ok",
        "service": "EPaperService",
        "simulation": is_simulation(),
        "display_mode": state.mode,
    }
