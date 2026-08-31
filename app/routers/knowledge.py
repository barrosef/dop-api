"""Rotas de conhecimento — tradução HTTP dos casos de uso, nada mais.

Zero decisão aqui: se você sentir vontade de escrever um `if` de regra nesta
camada, ele pertence a `app/usecases/knowledge.py`, senão a porta gRPC fica
sem ele. Autorização, idem — os decorators estão no caso de uso.
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

router = APIRouter(prefix="/api/v1", tags=["conhecimento"])

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
    """A bagagem de bordo do agente para esta demanda (ADR-0009 §3).

    `dropped` é o campo que importa nesta resposta: **`null` significa que o
    núcleo não informou o descarte** (hoje o contrato `dop.v1` não o carrega),
    e não "nada foi descartado". Quando vier preenchido, `dropped.truncated`
    diz em um campo só se o contexto coube inteiro — a tela precisa poder
    avisar que não coube (ADR-0012).
    """
    return await uc.get_context_package(demand_id)


@router.get("/knowledge/rules", response_model=list[str])
async def list_rules(project_id: str = Query(min_length=1)) -> list[str]:
    """As regras que valem para o projeto, com a herança já resolvida."""
    return await uc.list_rules(project_id)


@router.get("/knowledge/memory", response_model=list[MemoryHit])
async def search_memory(
    q: str = Query(min_length=1),
    project_id: str = "",
    limit: int = 0,
) -> list[MemoryHit]:
    """Busca na memória. `project_id` vazio busca a memória da CONTA.

    Cada resultado vem com o artefato e o seu score PAREADOS — `score: null`
    quando o núcleo não pontuou (busca lexical), que é diferente de zero.
    """
    return await uc.search_memory(project_id, q, limit)


@router.get("/knowledge/index", response_model=ArtifactSummary)
async def read_index(
    project_id: str = Query(min_length=1), repo: str = Query(min_length=1)
) -> ArtifactSummary:
    """O mapa do repositório. Sem índice é 404 — e 404 é a resposta útil."""
    return await uc.read_index(project_id, repo)


@router.post("/knowledge/artifacts", response_model=ArtifactSummary, status_code=201)
async def put_artifact(
    body: NewArtifact,
    idempotency_key: str = Header(default="", alias="Idempotency-Key"),
) -> ArtifactSummary:
    """Grava conhecimento. `project_id` vazio grava no escopo da conta.

    O conteúdo vai em `content_base64` porque no núcleo ele é `bytes`.
    """
    return await uc.put_artifact(body, idempotency_key)
