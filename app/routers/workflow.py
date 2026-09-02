"""Workflow routes — the use cases' HTTP translation, nothing more.

Zero decisions here: if a rule's `if` shows up in this layer, it belongs in
`app/usecases/workflow.py`, or the gRPC port ends up without it.
"""

from fastapi import APIRouter, Header

from app.usecases import workflow as uc
from app.usecases.workflow import (
    EffectiveFlow,
    Flow,
    NewFlow,
    PromotionTarget,
    Provenance,
    StageOrigin,
    StageSpec,
    ValidationReport,
)

router = APIRouter(prefix="/api/v1", tags=["workflow"])

__all__ = [
    "EffectiveFlow",
    "Flow",
    "NewFlow",
    "PromotionTarget",
    "Provenance",
    "StageOrigin",
    "StageSpec",
    "ValidationReport",
    "router",
]


@router.get("/flows", response_model=list[Flow])
async def list_flows(owner_scope: str = "", owner_id: str = "") -> list[Flow]:
    return await uc.list_flows(owner_scope, owner_id)


@router.post("/flows", response_model=Flow, status_code=201)
async def create_flow(
    body: NewFlow,
    idempotency_key: str = Header(default="", alias="Idempotency-Key"),
) -> Flow:
    return await uc.create_flow(body, idempotency_key)


# MIND the order: this route comes BEFORE /flows/{flow_id}, or "effective" and
# "validate" would be captured as a flow id. FastAPI matches in registration
# order, and the mistake would only show up at runtime, as an inexplicable 404.
@router.get("/flows/effective", response_model=EffectiveFlow)
async def resolve_flow(scope: str, scope_id: str = "") -> EffectiveFlow:
    """A level's effective flow AND the provenance trail, structured."""
    return await uc.resolve_flow(scope, scope_id)


@router.post("/flows/validate", response_model=ValidationReport)
async def validate_flow(body: NewFlow) -> ValidationReport:
    """A dry run: it returns the report, not an error — the screen marks the stages."""
    return await uc.validate_flow(body)


@router.get("/flows/{flow_id}", response_model=Flow)
async def get_flow(flow_id: str) -> Flow:
    return await uc.get_flow(flow_id)


@router.put("/flows/{flow_id}", response_model=Flow)
async def update_flow(flow_id: str, body: NewFlow) -> Flow:
    """Changing it produces a new version in the core; demands under way do not change."""
    return await uc.update_flow(flow_id, body)


@router.post("/flows/{flow_id}/promotion", response_model=Flow)
async def promote_flow(flow_id: str, body: PromotionTarget) -> Flow:
    return await uc.promote_flow(flow_id, body)
