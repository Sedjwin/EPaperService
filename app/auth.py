"""Validate principal identity by calling UserManager /auth/validate."""
from typing import Optional

import httpx
from fastapi import Header, HTTPException

from .config import settings


async def get_principal(authorization: Optional[str] = Header(default=None)) -> dict:
    """
    Dependency: validate a Bearer token (JWT or API key) against UserManager.
    Returns the principal dict on success, raises 401 on failure.
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Not authenticated — provide Authorization: Bearer <token>")
    token = authorization[7:]
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{settings.usermanager_url}/auth/validate",
                headers={"Authorization": f"Bearer {token}"},
                timeout=5.0,
            )
        data = r.json()
    except Exception:
        raise HTTPException(503, "UserManager unavailable")

    if not data.get("valid"):
        raise HTTPException(401, "Invalid or expired token")
    return data


async def get_optional_principal(authorization: Optional[str] = Header(default=None)) -> Optional[dict]:
    """Like get_principal but returns None instead of raising for unauthenticated requests."""
    if not authorization:
        return None
    try:
        return await get_principal(authorization)
    except HTTPException:
        return None
