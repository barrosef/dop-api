"""Sonda de saúde — pública por definição."""

from fastapi import APIRouter

from app.platform.security.decorator import public

router = APIRouter(tags=["health"])


@router.get("/healthz")
@public
async def healthz() -> dict:
    return {"status": "ok"}
