"""E-paper display booking API."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, field_validator
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_optional_principal, get_principal
from app.database import get_db
from app.models import Booking
from app.state import state

router = APIRouter(prefix="/api", tags=["Display"])
logger = logging.getLogger(__name__)


# ── helpers ───────────────────────────────────────────────────────────────────

def _now_naive() -> datetime:
    """Current UTC time as naive datetime (matches DB storage)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _to_naive(dt: datetime) -> datetime:
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _booking_out(b: Booking) -> dict:
    return {
        "booking_id":    b.booking_id,
        "principal_id":  b.principal_id,
        "principal_name": b.principal_name,
        "principal_type": b.principal_type,
        "content_type":  b.content_type,
        "start_time":    b.start_time.isoformat() + "Z",
        "end_time":      b.end_time.isoformat() + "Z",
        "description":   b.description,
        "cancelled":     b.cancelled,
        "created_at":    b.created_at.isoformat() + "Z",
    }


# ── request schemas ───────────────────────────────────────────────────────────

class BookingRequest(BaseModel):
    content_type: str          # "markdown" | "svg" | "image"
    content: str               # raw markdown/SVG text or base64-encoded image
    start_time: datetime
    end_time: datetime
    description: str = ""

    @field_validator("content_type")
    @classmethod
    def validate_content_type(cls, v: str) -> str:
        if v not in ("markdown", "svg", "image"):
            raise ValueError("content_type must be 'markdown', 'svg', or 'image'")
        return v

    @field_validator("end_time")
    @classmethod
    def end_after_start(cls, v: datetime, info) -> datetime:
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
        "mode":             state.mode,
        "current_booking":  _booking_out(active) if active else None,
        "last_updated":     state.last_updated,
        "simulation":       is_simulation(),
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
    Returns 409 if the requested window conflicts with an existing booking,
    including the conflicting booking details so agents can negotiate.
    """
    start = _to_naive(req.start_time)
    end   = _to_naive(req.end_time)
    now   = _now_naive()

    if end <= now:
        raise HTTPException(400, "end_time is in the past")

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
                "error": "conflict",
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
    """
    Cancel a booking. Only the booking's owner or an admin may cancel.
    """
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
            f"Only the booking owner ({booking.principal_name}) or an admin can cancel this booking.",
        )

    booking.cancelled = True
    booking.cancelled_by = principal["user_id"]
    await db.commit()

    # If this is the currently active booking, revert to idle immediately
    if state.current_booking_id == booking_id:
        from app.main import refresh_stats
        import asyncio
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
async def get_history(
    limit: int = 100,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
):
    """All bookings (past + future + cancelled), newest first. Used by calendar UI."""
    result = await db.execute(
        select(Booking)
        .order_by(Booking.start_time.desc())
        .limit(limit)
        .offset(offset)
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


# ── proxy auth login (for admin panel) ───────────────────────────────────────

@router.post("/auth/login")
async def proxy_login(body: dict):
    """Proxy UserManager login so the admin panel doesn't need to know the UM URL."""
    import httpx
    from app.config import settings
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"{settings.usermanager_url}/auth/login",
                json=body,
                timeout=5.0,
            )
        return r.json()
    except Exception as exc:
        raise HTTPException(503, f"UserManager unavailable: {exc}")
