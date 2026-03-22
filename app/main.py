"""
EPaperService — FastAPI entry point.

Controls the Waveshare 3.6inch e-Paper HAT+ (E) (600×400, 6-colour Spectra 6).
- Idle mode: refreshes stats at configurable intervals (default every 20 min).
- Booking mode: displays agent/user-booked content for the reserved time window.
"""

import asyncio
import io
import logging
import time
import urllib.parse
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

import httpx
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select, func

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

# ── weather cache ─────────────────────────────────────────────────────────────

_weather_cache: dict[str, dict] = {}
WEATHER_TTL = 600  # seconds


async def _fetch_weather(location: str) -> dict | None:
    key = location.lower()
    now = time.time()
    cached = _weather_cache.get(key)
    if cached and now - cached["ts"] < WEATHER_TTL:
        return cached["data"]
    try:
        url = f"https://wttr.in/{urllib.parse.quote(location)}?format=j1"
        async with httpx.AsyncClient() as client:
            r = await client.get(url, timeout=6.0)
            data = r.json()
        cond = data["current_condition"][0]
        result = {
            "temp_c":    int(cond["temp_C"]),
            "condition": cond["weatherDesc"][0]["value"],
            "humidity":  int(cond["humidity"]),
            "location":  location,
        }
        _weather_cache[key] = {"data": result, "ts": now}
        return result
    except Exception as exc:
        logger.debug("Weather fetch failed: %s", exc)
        return cached["data"] if cached else None


# ── service health checks ─────────────────────────────────────────────────────

async def _check_services(services_cfg: list[dict]) -> list[dict]:
    results = []
    async with httpx.AsyncClient() as client:
        for svc in services_cfg:
            try:
                r = await client.get(svc["url"], timeout=2.0)
                up = r.status_code < 500
            except Exception:
                up = False
            results.append({"name": svc["name"], "up": up})
    return results


# ── booking stats ─────────────────────────────────────────────────────────────

async def _get_booking_stats() -> dict:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    async with AsyncSessionLocal() as db:
        cnt = await db.scalar(
            select(func.count()).where(
                Booking.cancelled == False,  # noqa: E712
                Booking.end_time > now,
            )
        )
        result = await db.execute(
            select(Booking)
            .where(Booking.cancelled == False,  # noqa: E712
                   Booking.start_time > now)
            .order_by(Booking.start_time)
            .limit(1)
        )
        next_b = result.scalar_one_or_none()
    return {
        "count": cnt or 0,
        "next": {
            "principal_name": next_b.principal_name,
            "start_time":     next_b.start_time,
            "description":    next_b.description,
        } if next_b else None,
    }


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
    """Render and push the idle stats screen using the current idle config."""
    from . import renderer
    from . import idle_config

    cfg = idle_config.load()

    # Gather async data based on which widgets are active
    widgets = cfg.get("auto_widgets", []) if cfg.get("mode") == "auto" else []
    extra: dict = {}

    if "weather" in widgets:
        loc = cfg.get("weather_location", "London")
        extra["weather"] = await _fetch_weather(loc)

    if "active_services" in widgets:
        extra["services"] = await _check_services(
            cfg.get("services_to_check", []))

    if "next_booking" in widgets or "booking_count" in widgets:
        stats = await _get_booking_stats()
        extra["next_booking"]  = stats["next"]
        extra["booking_count"] = stats["count"]

    if "quote" in widgets:
        extra["quote"] = idle_config.next_quote(cfg)

    logger.info("Refreshing idle stats display")
    img = await asyncio.to_thread(renderer.render_stats, cfg, extra)
    state.mode = "idle"
    state.current_booking_id = None
    state.agent_name = None
    await push_image(img)


def render_booking(booking: Booking):
    """Render a booking's content to a PIL image (synchronous, run in thread)."""
    from . import renderer
    from datetime import timezone as tz

    start = (booking.start_time.replace(tzinfo=tz.utc)
             if booking.start_time.tzinfo is None else booking.start_time)
    end   = (booking.end_time.replace(tzinfo=tz.utc)
             if booking.end_time.tzinfo is None else booking.end_time)

    if booking.content_type == "svg":
        return renderer.render_svg(booking.content, booking.principal_name, start, end)
    elif booking.content_type == "image":
        return renderer.render_image(booking.content, booking.principal_name, start, end)
    else:
        return renderer.render_markdown(booking.content, booking.principal_name, start, end)


# ── booking query ─────────────────────────────────────────────────────────────

async def get_active_booking() -> Booking | None:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
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
    from . import idle_config

    await refresh_stats()
    last_stats_refresh = time.time()

    while True:
        try:
            active = await get_active_booking()
            cfg    = idle_config.load()
            interval_secs = cfg.get("idle_interval_mins", 20) * 60

            if active:
                if active.booking_id != state.current_booking_id:
                    logger.info("Activating booking %s by %s",
                                active.booking_id, active.principal_name)
                    state.mode = "booked"
                    state.current_booking_id = active.booking_id
                    state.agent_name = active.principal_name
                    img = await asyncio.to_thread(render_booking, active)
                    await push_image(img, booking=active)
            else:
                if state.current_booking_id is not None:
                    logger.info("Booking %s expired, reverting to idle",
                                state.current_booking_id)
                    await refresh_stats()
                    last_stats_refresh = time.time()
                elif time.time() - last_stats_refresh >= interval_secs:
                    await refresh_stats()
                    last_stats_refresh = time.time()

        except Exception as exc:
            logger.error("Display loop error: %s", exc)

        await asyncio.sleep(30)


# ── lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    from . import idle_config as ic
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "bookings").mkdir(parents=True, exist_ok=True)
    ic.init(DATA_DIR)
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
    version="3.0.0",
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
        "status":          "ok",
        "service":         "EPaperService",
        "simulation":      is_simulation(),
        "display_mode":    state.mode,
        "current_booking": state.current_booking_id,
    }
