"""Demand routes — the use cases' HTTP translation, nothing more.

Zero decisions here: if a rule's `if` shows up in this layer, it belongs in
`app/usecases/demand.py`, or the gRPC port ends up without it.
"""

from fastapi import APIRouter, Header

from app.usecases import demand as uc
from app.usecases.demand import (
    Demand,
    DemandCockpit,
    DemandPage,
    Finding,
    GateDecision,
    Message,
    NewDemand,
    NewFinding,
    NewMessage,
    NewThread,
    Stage,
    StageTransition,
    Thread,
)

router = APIRouter(prefix="/api/v1", tags=["demand"])

__all__ = [
    "Demand",
    "DemandCockpit",
    "DemandPage",
    "Finding",
    "GateDecision",
    "Message",
    "NewDemand",
    "NewFinding",
    "NewMessage",
    "NewThread",
    "Stage",
    "StageTransition",
    "Thread",
    "router",
]


@router.get("/demands", response_model=DemandPage)
async def list_demands(
    project_id: str = "", page_size: int = 0, page_token: str = ""
) -> DemandPage:
    return await uc.list_demands(project_id, page_size, page_token)


@router.post("/demands", response_model=Demand, status_code=201)
async def start_demand(
    body: NewDemand,
    idempotency_key: str = Header(default="", alias="Idempotency-Key"),
) -> Demand:
    """Inicia a demanda a partir do card do provedor — resolve e congela o fluxo."""
    return await uc.start_demand(body, idempotency_key)


@router.get("/demands/{demand_id}", response_model=Demand)
async def get_demand(demand_id: str) -> Demand:
    return await uc.get_demand(demand_id)


@router.get("/demands/{demand_id}/cockpit", response_model=DemandCockpit)
async def get_cockpit(demand_id: str) -> DemandCockpit:
    """The demand's whole screen: stages, threads and findings in one response."""
    return await uc.get_cockpit(demand_id)


@router.post("/demands/{demand_id}/stages/{stage_key}/advance", response_model=Stage)
async def advance_stage(
    demand_id: str,
    stage_key: str,
    body: StageTransition,
    idempotency_key: str = Header(default="", alias="Idempotency-Key"),
) -> Stage:
    return await uc.advance_stage(demand_id, stage_key, body, idempotency_key)


@router.post("/demands/{demand_id}/stages/{stage_key}/gate", response_model=Stage)
async def decide_gate(
    demand_id: str,
    stage_key: str,
    body: GateDecision,
    idempotency_key: str = Header(default="", alias="Idempotency-Key"),
) -> Stage:
    """The human gate's decision — approving finishes it, rejecting blocks it."""
    return await uc.decide_gate(demand_id, stage_key, body, idempotency_key)


@router.get("/demands/{demand_id}/threads", response_model=list[Thread])
async def list_threads(demand_id: str) -> list[Thread]:
    return await uc.list_threads(demand_id)


@router.post("/demands/{demand_id}/threads", response_model=Thread, status_code=201)
async def create_thread(
    demand_id: str,
    body: NewThread,
    idempotency_key: str = Header(default="", alias="Idempotency-Key"),
) -> Thread:
    return await uc.create_thread(demand_id, body, idempotency_key)


@router.post("/demands/{demand_id}/findings", response_model=Finding, status_code=201)
async def publish_finding(
    demand_id: str,
    body: NewFinding,
    idempotency_key: str = Header(default="", alias="Idempotency-Key"),
) -> Finding:
    return await uc.publish_finding(demand_id, body, idempotency_key)


# The message belongs to the THREAD, not to the demand: the route follows the
# resource's owner, or the client has to carry the demand around just to write in
# a conversation.
@router.post("/threads/{thread_id}/messages", response_model=Message, status_code=201)
async def post_message(
    thread_id: str,
    body: NewMessage,
    idempotency_key: str = Header(default="", alias="Idempotency-Key"),
) -> Message:
    return await uc.post_message(thread_id, body, idempotency_key)
