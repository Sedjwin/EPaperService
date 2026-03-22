"""
EPaperService — FastAPI entry point.

Controls the Waveshare 3.6inch e-Paper HAT+ (E) (600×400, 6-colour Spectra 6).
- Idle mode: refreshes stats at :00/:20/:40 of every hour.
- Booking mode: displays agent/user-booked content for the reserved time window.
"""

import asyncio
import io
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select

from .config import settings
from .database import init_db, get_db, AsyncSessionLocal
from .models import Booking
from .routers.epaper import router as epaper_router
from .state import state

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
)
logger = logging.getLogger(__name__)

_STATIC  = Path(__file__).parent / "static"
DATA_DIR = Path(__file__).parent.parent / "data"


# ── image helpers ─────────────────────────────────────────────────────────────

def _booking_img_path(booking_id: str) -> Path:
    return DATA_DIR / "bookings" / f"{booking_id}.png"


def _save_booking_png(booking_id: str, png: bytes) -> None:
    p = _booking_img_path(booking_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(png)


# ── display helpers ───────────────────────────────────────────────────────────

async def push_image(img, booking: Booking | None = None) -> None:
    """Push a PIL image to the display (non-blocking). Saves PNG for preview."""
    from . import display

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    png = buf.getvalue()

    state.current_image_png = png
    state.last_updated = datetime.now(timezone.utc).timestamp()

    if booking:
        _save_booking_png(booking.booking_id, png)

    await asyncio.to_thread(display.show, img)


async def refresh_stats() -> None:
    """Render and push the idle stats screen (not logged to booking history)."""
    from . import renderer
    logger.info("Refreshing idle stats display")
    img = await asyncio.to_thread(renderer.render_stats)
    state.mode = "idle"
    state.current_booking_id = None
    state.agent_name = None
    await push_image(img)


def render_booking(booking: Booking):
    """Render a booking's content to a PIL image (synchronous, run in thread)."""
    from . import renderer
    from datetime import timezone as tz

    start = booking.start_time.replace(tzinfo=tz.utc) if booking.start_time.tzinfo is None else booking.start_time
    end   = booking.end_time.replace(tzinfo=tz.utc)   if booking.end_time.tzinfo is None else booking.end_time

    if booking.content_type == "svg":
        return renderer.render_svg(booking.content, booking.principal_name, start, end)
    elif booking.content_type == "image":
        return renderer.render_image(booking.content, booking.principal_name, start, end)
    else:  # markdown (default)
        return renderer.render_markdown(booking.content, booking.principal_name, start, end)


# ── booking query ─────────────────────────────────────────────────────────────

async def get_active_booking() -> Booking | None:
    """Return the active (non-cancelled, within time window) booking, or None."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)  # DB stores naive UTC
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Booking)
            .where(
                Booking.cancelled == False,  # noqa: E712
                Booking.start_time <= now,
                Booking.end_time > now,
            )
            .order_by(Booking.start_time)
            .limit(1)
        )
        return result.scalar_one_or_none()


# ── display loop ──────────────────────────────────────────────────────────────

async def _display_loop() -> None:
    """
    Unified display loop:
    - Activates bookings at their start_time
    - Reverts to idle on booking expiry
    - Refreshes idle stats every idle_interval_min minutes
    """
    import time
    last_stats_refresh = 0.0

    await refresh_stats()
    last_stats_refresh = time.time()

    while True:
        try:
            active = await get_active_booking()

            if active:
                if active.booking_id != state.current_booking_id:
                    logger.info("Activating booking %s by %s", active.booking_id, active.principal_name)
                    state.mode = "booked"
                    state.current_booking_id = active.booking_id
                    state.agent_name = active.principal_name
                    img = await asyncio.to_thread(render_booking, active)
                    await push_image(img, booking=active)
            else:
                if state.current_booking_id is not None:
                    logger.info("Booking %s expired, reverting to idle", state.current_booking_id)
                    await refresh_stats()
                    last_stats_refresh = time.time()
                elif time.time() - last_stats_refresh >= settings.idle_interval_min * 60:
                    await refresh_stats()
                    last_stats_refresh = time.time()
        except Exception as exc:
            logger.error("Display loop error: %s", exc)

        await asyncio.sleep(30)


# ── lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "bookings").mkdir(parents=True, exist_ok=True)
    await init_db()
    logger.info("EPaperService starting on %s:%d", settings.host, settings.port)
    task = asyncio.create_task(_display_loop())
    yield
    task.cancel()
    logger.info("EPaperService shutting down.")


# ── app ───────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="EPaperService",
    description="Controls the Waveshare 3.6inch e-Paper HAT+ (E) display with a booking schedule.",
    version="2.0.0",
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
        "current_booking": state.current_booking_id,
    }
