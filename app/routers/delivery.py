"""Delivery routes — the use cases' HTTP translation, nothing more.

Zero decisions here. The only thing this layer settles on its own is the STATUS
STATUS da recusa da fila de merge: no HTTP o status faz parte da resposta, e é
of the refusal, because the status is part of the HTTP response and it is the
adapter that knows that. The refusal's content — the list of what is missing for
the commit to be green — comes ready from the use case, the same for both ports.
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

router = APIRouter(prefix="/api/v1", tags=["delivery"])

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
    """The project's PRs and directives in one response — the delivery screen."""
    return await uc.get_board(project_id)


@router.get("/pull-requests", response_model=list[PullRequest])
async def list_pull_requests(demand_id: str = "", project_id: str = "") -> list[PullRequest]:
    return await uc.list_pull_requests(demand_id, project_id)


@router.get("/repos/{repo_id}/merge-queue", response_model=list[MergeQueueEntry])
async def get_merge_queue(repo_id: str) -> list[MergeQueueEntry]:
    """ONE repository's queue — it is the repository that serializes (ADR-0005)."""
    return await uc.get_merge_queue(repo_id)


@router.post(
    "/repos/{repo_id}/merge-queue",
    status_code=201,
    response_model=None,
    responses={
        201: {"description": "it entered the queue"},
        412: {"description": "refused for want of green, with the list of what is missing"},
    },
)
async def enqueue_merge(
    repo_id: str,
    body: NewMergeEntry,
    idempotency_key: str = Header(default="", alias="Idempotency-Key"),
) -> JSONResponse:
    """Asks to enter the repository's queue.

    A refusal for want of green comes out as a 412 — the same thing the core's
    FAILED_PRECONDITION becomes on any other route (`app/platform/errors.py`) —
    but with the body carrying `missing` item by item, instead of a single
    sentence. It is that list that tells the dev what to arrange in order to get
    in (ADR-0005).
    """
    attempt = await uc.enqueue_merge(repo_id, body, idempotency_key)
    if attempt.refusal is not None:
        return JSONResponse(
            status_code=412, content={"detail": attempt.refusal.model_dump()}
        )
    return JSONResponse(status_code=201, content=attempt.entry.model_dump())


@router.get("/directives", response_model=list[Directive])
async def list_directives(project_id: str) -> list[Directive]:
    return await uc.list_directives(project_id)


@router.post("/directives/{directive_id}/decision", response_model=Directive)
async def decide_directive(
    directive_id: str,
    body: DirectiveDecision,
    idempotency_key: str = Header(default="", alias="Idempotency-Key"),
) -> Directive:
    """The coordination decision is the dev's; the techlead recommends (ADR-0011)."""
    return await uc.decide_directive(directive_id, body, idempotency_key)
