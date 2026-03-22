"""E-paper display booking API."""
from __future__ import annotations

import asyncio
import io
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_optional_principal, get_principal
from app.database import get_db
from app.models import Booking
from app.state import state

router = APIRouter(prefix="/api", tags=["Display"])
logger = logging.getLogger(__name__)


# ── helpers ───────────────────────────────────────────────────────────────────

def _now_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _to_naive(dt: datetime) -> datetime:
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _booking_out(b: Booking) -> dict:
    return {
        "booking_id":     b.booking_id,
        "principal_id":   b.principal_id,
        "principal_name": b.principal_name,
        "principal_type": b.principal_type,
        "content_type":   b.content_type,
        "start_time":     b.start_time.isoformat() + "Z",
        "end_time":       b.end_time.isoformat() + "Z",
        "description":    b.description,
        "cancelled":      b.cancelled,
        "created_at":     b.created_at.isoformat() + "Z",
    }


# ── request schemas ───────────────────────────────────────────────────────────

class BookingRequest(BaseModel):
    content_type: str
    content: str
    start_time: Optional[datetime] = None   # None → now
    end_time:   Optional[datetime] = None   # None → computed from apply_now config
    description: str = ""

    @field_validator("content_type")
    @classmethod
    def validate_content_type(cls, v: str) -> str:
        if v not in ("markdown", "svg", "image"):
            raise ValueError("content_type must be 'markdown', 'svg', or 'image'")
        return v

    @field_validator("end_time")
    @classmethod
    def end_after_start(cls, v: Optional[datetime], info) -> Optional[datetime]:
        if v is None:
            return v
        start = info.data.get("start_time")
        if start and _to_naive(v) <= _to_naive(start):
            raise ValueError("end_time must be after start_time")
        return v


# ── status / preview ──────────────────────────────────────────────────────────

@router.get("/status")
async def get_status():
    from app.display import is_simulation
    from app.main import get_active_booking
    active = await get_active_booking()
    return {
        "mode":            state.mode,
        "current_booking": _booking_out(active) if active else None,
        "last_updated":    state.last_updated,
        "simulation":      is_simulation(),
    }


@router.get("/preview")
async def preview():
    """Return the current display frame as PNG. 204 if nothing displayed yet."""
    if state.current_image_png is None:
        raise HTTPException(status_code=204, detail="No image yet")
    return Response(content=state.current_image_png, media_type="image/png")


# ── booking endpoints ─────────────────────────────────────────────────────────

@router.post("/book", status_code=201)
async def create_booking(
    req: BookingRequest,
    principal: dict = Depends(get_principal),
    db: AsyncSession = Depends(get_db),
):
    """
    Book a display time slot. Auth: UserManager JWT or agent API key.
    start_time defaults to now; end_time defaults to apply_now config rule.
    Returns 409 if the requested window conflicts with an existing booking.
    """
    now   = _now_naive()
    start = _to_naive(req.start_time) if req.start_time else now

    if req.end_time:
        end = _to_naive(req.end_time)
    else:
        from app import idle_config
        cfg = idle_config.load()
        end = idle_config.compute_end_time(cfg, start)

    if end <= now:
        raise HTTPException(400, "end_time is in the past")
    if end <= start:
        raise HTTPException(400, "end_time must be after start_time")

    # Conflict check
    result = await db.execute(
        select(Booking).where(
            Booking.cancelled == False,  # noqa: E712
            Booking.end_time > start,
            Booking.start_time < end,
        )
    )
    conflicts = result.scalars().all()
    if conflicts:
        raise HTTPException(
            status_code=409,
            detail={
                "error":   "conflict",
                "message": "The requested time slot overlaps with existing booking(s).",
                "conflicts": [
                    {
                        "booking_id":    c.booking_id,
                        "principal_name": c.principal_name,
                        "principal_type": c.principal_type,
                        "start_time":    c.start_time.isoformat() + "Z",
                        "end_time":      c.end_time.isoformat() + "Z",
                        "description":   c.description,
                    }
                    for c in conflicts
                ],
            },
        )

    description = req.description or f"{principal['username']} — {req.content_type}"
    booking = Booking(
        principal_id=principal["user_id"],
        principal_name=principal.get("display_name") or principal["username"],
        principal_type=principal["principal_type"],
        content_type=req.content_type,
        content=req.content,
        start_time=start,
        end_time=end,
        description=description,
    )
    db.add(booking)
    await db.commit()
    await db.refresh(booking)

    logger.info("Booking %s created by %s for %s – %s",
                booking.booking_id, booking.principal_name,
                start.isoformat(), end.isoformat())

    return _booking_out(booking)


@router.delete("/book/{booking_id}", status_code=204)
async def cancel_booking(
    booking_id: str,
    principal: dict = Depends(get_principal),
    db: AsyncSession = Depends(get_db),
):
    """Cancel a booking. Only the booking's owner or an admin may cancel."""
    booking = await db.get(Booking, booking_id)
    if not booking:
        raise HTTPException(404, "Booking not found")
    if booking.cancelled:
        raise HTTPException(400, "Booking is already cancelled")

    is_owner = booking.principal_id == principal["user_id"]
    is_admin  = principal.get("is_admin", False)
    if not is_owner and not is_admin:
        raise HTTPException(
            403,
            f"Only the booking owner ({booking.principal_name}) or an admin can cancel.",
        )

    booking.cancelled   = True
    booking.cancelled_by = principal["user_id"]
    await db.commit()

    if state.current_booking_id == booking_id:
        from app.main import refresh_stats
        asyncio.create_task(refresh_stats())

    logger.info("Booking %s cancelled by %s", booking_id, principal["username"])


# ── schedule view ─────────────────────────────────────────────────────────────

@router.get("/schedule")
async def get_schedule(db: AsyncSession = Depends(get_db)):
    """Active + upcoming bookings (not cancelled)."""
    now = _now_naive()
    result = await db.execute(
        select(Booking)
        .where(Booking.cancelled == False, Booking.end_time > now)  # noqa: E712
        .order_by(Booking.start_time)
    )
    return [_booking_out(b) for b in result.scalars().all()]


@router.get("/history")
async def get_history(limit: int = 100, offset: int = 0,
                      db: AsyncSession = Depends(get_db)):
    """All bookings (past + future + cancelled), newest first."""
    result = await db.execute(
        select(Booking)
        .order_by(Booking.start_time.desc())
        .limit(limit).offset(offset)
    )
    return [_booking_out(b) for b in result.scalars().all()]


@router.get("/history/{booking_id}/image")
async def booking_image(booking_id: str, db: AsyncSession = Depends(get_db)):
    """Return the rendered PNG for a past booking (if saved)."""
    from app.main import _booking_img_path
    booking = await db.get(Booking, booking_id)
    if not booking:
        raise HTTPException(404, "Booking not found")
    path = _booking_img_path(booking_id)
    if not path.exists():
        raise HTTPException(404, "No saved image for this booking yet")
    return Response(content=path.read_bytes(), media_type="image/png")


# ── idle screen config ────────────────────────────────────────────────────────

@router.get("/idle-config")
async def get_idle_config():
    """Return the current idle screen configuration."""
    from app import idle_config
    cfg = idle_config.load()
    # Don't expose potentially large image_b64 in a GET — send a flag instead
    out = dict(cfg)
    if out.get("image_b64"):
        out["image_b64"] = "__has_image__"
    return out


@router.post("/idle-config")
async def set_idle_config(
    body: dict,
    principal: dict = Depends(get_principal),
):
    """Save the idle screen configuration. Auth required."""
    from app import idle_config
    cfg = idle_config.load()
    # Preserve quote_index unless explicitly sent
    body.setdefault("quote_index", cfg.get("quote_index", 0))
    idle_config.save(body)
    logger.info("Idle config updated by %s", principal["username"])
    return {"ok": True}


@router.post("/idle-preview")
async def idle_preview(body: dict | None = None):
    """
    Render a preview of the idle screen with the given config (or saved config).
    No auth required — read-only preview.
    """
    from app import idle_config, renderer
    from app.main import (_fetch_weather, _check_services, _get_booking_stats)

    cfg = body if body else idle_config.load()

    # Resolve __has_image__ placeholder
    if cfg.get("image_b64") == "__has_image__":
        saved = idle_config.load()
        cfg["image_b64"] = saved.get("image_b64", "")

    widgets = cfg.get("auto_widgets", []) if cfg.get("mode") == "auto" else []
    extra: dict = {}

    if "weather" in widgets:
        extra["weather"] = await _fetch_weather(
            cfg.get("weather_location", "London"))

    if "active_services" in widgets:
        extra["services"] = await _check_services(
            cfg.get("services_to_check", []))

    if "next_booking" in widgets or "booking_count" in widgets:
        stats = await _get_booking_stats()
        extra["next_booking"]  = stats["next"]
        extra["booking_count"] = stats["count"]

    # Use the next quote without advancing the index
    if "quote" in widgets:
        idx = int(cfg.get("quote_index", 0)) % len(idle_config.QUOTES)
        extra["quote"] = idle_config.QUOTES[idx]

    img = await asyncio.to_thread(renderer.render_stats, cfg, extra)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return Response(content=buf.getvalue(), media_type="image/png")


# ── proxy auth login (for admin panel) ───────────────────────────────────────

@router.post("/auth/login")
async def proxy_login(body: dict):
    """Proxy UserManager login so the admin panel doesn't need to know the UM URL."""
    import httpx as _httpx
    from app.config import settings
    try:
        async with _httpx.AsyncClient() as client:
            r = await client.post(
                f"{settings.usermanager_url}/auth/login",
                json=body, timeout=5.0,
            )
        return r.json()
    except Exception as exc:
        raise HTTPException(503, f"UserManager unavailable: {exc}")
