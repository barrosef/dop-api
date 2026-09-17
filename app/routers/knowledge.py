"""Knowledge routes — the use cases' HTTP translation, nothing more.

Zero decisions here: if you feel the urge to write a rule's `if` in this layer,
it belongs in `app/usecases/knowledge.py`, or the gRPC port ends up without it.
Authorization likewise — the decorators are in the use case.
"""

from fastapi import APIRouter, Header, Query

from app.usecases import knowledge as uc
from app.usecases.knowledge import (
    ArtifactSummary,
    ContextPackageSummary,
    DroppedCounts,
    FindingSummary,
    MemoryHit,
    NewArtifact,
)

router = APIRouter(prefix="/api/v1", tags=["knowledge"])

__all__ = [
    "ArtifactSummary",
    "ContextPackageSummary",
    "DroppedCounts",
    "FindingSummary",
    "MemoryHit",
    "NewArtifact",
    "router",
]


@router.get("/demands/{demand_id}/context-package", response_model=ContextPackageSummary)
async def get_context_package(demand_id: str) -> ContextPackageSummary:
    """The agent's carry-on luggage for this demand (ADR-0006 §3).

    `dropped` is the field that matters in this response: **`null` means the core
    did not report the drops** (today the `dop.v1` contract does not carry them),
    and not "nothing was dropped". When it comes filled in, `dropped.truncated`
    says in a single field whether the context fitted whole — the screen has to be
    able to warn that it did not (ADR-0008).
    """
    return await uc.get_context_package(demand_id)


@router.get("/knowledge/rules", response_model=list[str])
async def list_rules(project_id: str = Query(min_length=1)) -> list[str]:
    """The rules that hold for the project, with the inheritance already resolved."""
    return await uc.list_rules(project_id)


@router.get("/knowledge/memory", response_model=list[MemoryHit])
async def search_memory(
    q: str = Query(min_length=1),
    project_id: str = "",
    limit: int = 0,
) -> list[MemoryHit]:
    """Searches the memory. An empty `project_id` searches the ACCOUNT's memory.

    Each result comes with the artifact and its score PAIRED — `score: null` when
    the core did not score it (a lexical search), which is different from zero.
    """
    return await uc.search_memory(project_id, q, limit)


@router.get("/knowledge/index", response_model=ArtifactSummary)
async def read_index(
    project_id: str = Query(min_length=1), repo: str = Query(min_length=1)
) -> ArtifactSummary:
    """The repository's map. With no index it is a 404 — and a 404 is the useful answer."""
    return await uc.read_index(project_id, repo)


@router.post("/knowledge/artifacts", response_model=ArtifactSummary, status_code=201)
async def put_artifact(
    body: NewArtifact,
    idempotency_key: str = Header(default="", alias="Idempotency-Key"),
) -> ArtifactSummary:
    """Writes knowledge. An empty `project_id` writes into the account's scope.

    The content goes in `content_base64` because in the core it is `bytes`.
    """
    return await uc.put_artifact(body, idempotency_key)
