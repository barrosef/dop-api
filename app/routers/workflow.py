"""Rotas de fluxo de trabalho — tradução HTTP dos casos de uso, nada mais.

Zero decisão aqui: se aparecer um `if` de regra nesta camada, ele pertence a
`app/usecases/workflow.py`, senão a porta gRPC fica sem ele.
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

router = APIRouter(prefix="/api/v1", tags=["fluxo"])

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


# ATENÇÃO à ordem: esta rota vem ANTES de /flows/{flow_id}, senão "effective" e
# "validate" seriam capturados como id de fluxo. O FastAPI casa na ordem de
# registro, e o erro só apareceria em runtime, como um 404 inexplicável.
@router.get("/flows/effective", response_model=EffectiveFlow)
async def resolve_flow(scope: str, scope_id: str = "") -> EffectiveFlow:
    """O fluxo efetivo de um nível E o rastro de procedência, estruturado."""
    return await uc.resolve_flow(scope, scope_id)


@router.post("/flows/validate", response_model=ValidationReport)
async def validate_flow(body: NewFlow) -> ValidationReport:
    """Ensaio: devolve o relatório, não um erro — a tela marca as etapas."""
    return await uc.validate_flow(body)


@router.get("/flows/{flow_id}", response_model=Flow)
async def get_flow(flow_id: str) -> Flow:
    return await uc.get_flow(flow_id)


@router.put("/flows/{flow_id}", response_model=Flow)
async def update_flow(flow_id: str, body: NewFlow) -> Flow:
    """Alterar gera versão nova no núcleo; demandas em andamento não mudam."""
    return await uc.update_flow(flow_id, body)


@router.post("/flows/{flow_id}/promotion", response_model=Flow)
async def promote_flow(flow_id: str, body: PromotionTarget) -> Flow:
    return await uc.promote_flow(flow_id, body)
