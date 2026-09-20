"""Catalog routes — an HTTP adapter over `app/usecases/catalog.py`."""

from fastapi import APIRouter

from app.usecases import catalog as uc
from app.usecases.catalog import PlanSummary, ProviderSummary

__all__ = ["PlanSummary", "ProviderSummary", "router"]

router = APIRouter(prefix="/api/v1", tags=["catalog"])


@router.get("/catalog/plans", response_model=list[PlanSummary])
async def list_plans() -> list[PlanSummary]:
    """The plans, without a price — the absence is deliberate (spec D-6)."""
    return await uc.plans()


@router.get("/catalog/providers", response_model=list[ProviderSummary])
async def list_providers() -> list[ProviderSummary]:
    """The providers a connection may point at, with the honest `operated` flag."""
    return await uc.providers()
