"""E-paper display REST API."""

import asyncio
import base64
import io
import time

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from app.state import state

router = APIRouter(prefix="/api", tags=["Display"])


class ShowRequest(BaseModel):
    type: str = "message"
    agent: str = "Agent"
    title: str = ""
    body: str = ""
    lines: list[str] = []
    color: str = "blue"
    duration: int | None = None


class DisplayImageRequest(BaseModel):
    image_b64: str            # base64-encoded PNG from browser canvas
    description: str = "upload"
    agent: str | None = None
    duration: int | None = None


class StatusResponse(BaseModel):
    mode: str
    agent_name: str | None
    last_updated: float
    simulation: bool
    next_refresh: float | None


@router.get("/status", response_model=StatusResponse)
async def get_status():
    from app.display import is_simulation
    from app.main import next_refresh_ts
    return StatusResponse(
        mode=state.mode,
        agent_name=state.agent_name,
        last_updated=state.last_updated,
        simulation=is_simulation(),
        next_refresh=next_refresh_ts() if state.mode == "idle" else None,
    )


@router.get("/preview")
async def preview():
    """Return the current display frame as PNG. 204 if nothing displayed yet."""
    if state.current_image_png is None:
        raise HTTPException(status_code=204, detail="No image yet")
    return Response(content=state.current_image_png, media_type="image/png")


@router.post("/show", status_code=202)
async def show(req: ShowRequest):
    from app import main as svc
    from app import renderer

    if req.type == "list":
        img = renderer.render_list(req.agent, req.title, req.lines, req.color, req.duration)
    else:
        img = renderer.render_message(req.agent, req.title, req.body, req.color, req.duration)

    state.mode = "agent"
    state.agent_name = req.agent
    state.content = req.model_dump()
    state.expires_at = time.time() + req.duration if req.duration else None
    state.last_updated = time.time()

    asyncio.create_task(svc.push_image(
        img, mode="agent", agent=req.agent,
        description=f"agent: {req.agent} — {req.title or req.body[:40]}"
    ))
    return {"status": "accepted"}


@router.post("/display-image", status_code=202)
async def display_image(req: DisplayImageRequest):
    """Display a raw image from the admin UI (upload or drawing)."""
    from PIL import Image
    from app import main as svc

    try:
        png_bytes = base64.b64decode(req.image_b64)
        img = Image.open(io.BytesIO(png_bytes)).convert("RGB")
        img = img.resize((600, 400), Image.LANCZOS)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid image: {exc}")

    state.mode = "agent" if req.agent else "idle"
    state.agent_name = req.agent
    state.content = {"type": "image", "description": req.description}
    state.expires_at = time.time() + req.duration if req.duration else None
    state.last_updated = time.time()

    asyncio.create_task(svc.push_image(
        img, mode=req.description, agent=req.agent,
        description=req.description
    ))
    return {"status": "accepted"}


@router.post("/release", status_code=202)
async def release():
    from app import main as svc
    state.release()
    asyncio.create_task(svc.refresh_stats())
    return {"status": "released"}


@router.post("/refresh", status_code=202)
async def force_refresh():
    from app import main as svc
    asyncio.create_task(svc.refresh_stats())
    return {"status": "refreshing"}


@router.get("/history")
async def get_history():
    return [
        {"id": e.id, "timestamp": e.timestamp,
         "mode": e.mode, "agent": e.agent, "description": e.description}
        for e in reversed(state.history)   # newest first
    ]


@router.get("/history/{entry_id}/image")
async def history_image(entry_id: str):
    from app.main import _img_path
    path = _img_path(entry_id)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Not found")
    return Response(content=path.read_bytes(), media_type="image/png")


@router.delete("/history/{entry_id}", status_code=204)
async def delete_history(entry_id: str):
    from app.main import _img_path, _save_history_index
    state.history = [e for e in state.history if e.id != entry_id]
    _img_path(entry_id).unlink(missing_ok=True)
    _save_history_index()
