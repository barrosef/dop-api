"""Resource routes — the use cases' HTTP translation, nothing more."""

from fastapi import APIRouter, Header

from app.usecases import resource as uc
from app.usecases.resource import (
    GrantSummary,
    NewCredential,
    NewGrant,
    NewResource,
    ResourceSummary,
)

router = APIRouter(prefix="/api/v1", tags=["resources"])

__all__ = [
    "GrantSummary",
    "NewCredential",
    "NewGrant",
    "NewResource",
    "ResourceSummary",
    "router",
]


@router.get("/resources", response_model=list[ResourceSummary])
async def list_resources(kind: str = "") -> list[ResourceSummary]:
    return await uc.list_resources(kind)


@router.post("/resources", response_model=ResourceSummary, status_code=201)
async def create_resource(
    body: NewResource,
    idempotency_key: str = Header(default="", alias="Idempotency-Key"),
) -> ResourceSummary:
    return await uc.create_resource(body, idempotency_key)


@router.get("/resources/{resource_id}", response_model=ResourceSummary)
async def get_resource(resource_id: str) -> ResourceSummary:
    return await uc.get_resource(resource_id)


@router.put("/resources/{resource_id}/credential", response_model=ResourceSummary)
async def set_credential(resource_id: str, body: NewCredential) -> ResourceSummary:
    """The body carries the secret; the response, never."""
    return await uc.set_credential(resource_id, body)


@router.get("/members/{user_id}/grants", response_model=list[GrantSummary])
async def list_member_grants(user_id: str) -> list[GrantSummary]:
    """The resource grants of one member of the active account."""
    return await uc.list_member_grants(user_id)


@router.post("/grants", response_model=GrantSummary, status_code=201)
async def grant_resource(body: NewGrant) -> GrantSummary:
    return await uc.grant_resource(body)


@router.delete("/grants/{grant_id}", status_code=204)
async def revoke_grant(grant_id: str) -> None:
    await uc.revoke_grant(grant_id)
