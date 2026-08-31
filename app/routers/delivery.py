"""Rotas de entrega — tradução HTTP dos casos de uso, nada mais.

Zero decisão aqui. A única coisa que esta camada resolve sozinha é o CÓDIGO DE
STATUS da recusa da fila de merge: no HTTP o status faz parte da resposta, e é
o adaptador quem sabe disso. O conteúdo da recusa — a lista do que falta para o
commit estar verde — vem pronto do caso de uso, igual para as duas portas.
"""

from fastapi import APIRouter, Header
from fastapi.responses import JSONResponse

from app.usecases import delivery as uc
from app.usecases.delivery import (
    DeliveryBoard,
    Directive,
    DirectiveDecision,
    MergeAttempt,
    MergeQueueEntry,
    MergeRefusal,
    NewMergeEntry,
    PullRequest,
    Reviewer,
)

router = APIRouter(prefix="/api/v1", tags=["entrega"])

__all__ = [
    "DeliveryBoard",
    "Directive",
    "DirectiveDecision",
    "MergeAttempt",
    "MergeQueueEntry",
    "MergeRefusal",
    "NewMergeEntry",
    "PullRequest",
    "Reviewer",
    "router",
]


@router.get("/delivery/board", response_model=DeliveryBoard)
async def get_board(project_id: str) -> DeliveryBoard:
    """PRs e diretrizes do projeto numa resposta — a tela de entrega."""
    return await uc.get_board(project_id)


@router.get("/pull-requests", response_model=list[PullRequest])
async def list_pull_requests(demand_id: str = "", project_id: str = "") -> list[PullRequest]:
    return await uc.list_pull_requests(demand_id, project_id)


@router.get("/repos/{repo_id}/merge-queue", response_model=list[MergeQueueEntry])
async def get_merge_queue(repo_id: str) -> list[MergeQueueEntry]:
    """A fila de UM repositório — é o repositório que serializa (ADR-0008)."""
    return await uc.get_merge_queue(repo_id)


@router.post(
    "/repos/{repo_id}/merge-queue",
    status_code=201,
    response_model=None,
    responses={
        201: {"description": "entrou na fila"},
        412: {"description": "recusada por falta de verde, com a lista do que falta"},
    },
)
async def enqueue_merge(
    repo_id: str,
    body: NewMergeEntry,
    idempotency_key: str = Header(default="", alias="Idempotency-Key"),
) -> JSONResponse:
    """Pede entrada na fila do repositório.

    Recusa por falta de verde sai como 412 — o mesmo que o FAILED_PRECONDITION
    do núcleo vira em qualquer outra rota (`app/platform/errors.py`) — mas com o
    corpo carregando `missing` item por item, em vez de uma frase só. É essa
    lista que diz ao dev o que providenciar para conseguir entrar (ADR-0007).
    """
    tentativa = await uc.enqueue_merge(repo_id, body, idempotency_key)
    if tentativa.refusal is not None:
        return JSONResponse(
            status_code=412, content={"detail": tentativa.refusal.model_dump()}
        )
    return JSONResponse(status_code=201, content=tentativa.entry.model_dump())


@router.get("/directives", response_model=list[Directive])
async def list_directives(project_id: str) -> list[Directive]:
    return await uc.list_directives(project_id)


@router.post("/directives/{directive_id}/decision", response_model=Directive)
async def decide_directive(
    directive_id: str,
    body: DirectiveDecision,
    idempotency_key: str = Header(default="", alias="Idempotency-Key"),
) -> Directive:
    """A decisão de coordenação é do dev; o techlead recomenda (ADR-0015)."""
    return await uc.decide_directive(directive_id, body, idempotency_key)
