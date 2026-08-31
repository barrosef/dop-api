"""Casos de uso de entrega — PR, fila de merge e as diretrizes do techlead.

Mesma disciplina de `identity` e `hierarchy`: a regra vive aqui e só aqui;
`app/routers/delivery.py` traduz HTTP e `app/grpcapi/delivery.py` traduz
protobuf, os dois chamando estas MESMAS funções, com os decorators no caso de
uso (ver o docstring de `app/usecases/identity.py`).

O que este módulo acrescenta ao núcleo:

1. **A recusa por falta de verde, item por item.** O núcleo cobra a ADR-0007 na
   porta da fila e recusa com FAILED_PRECONDITION mais uma frase que LISTA o
   que falta ("nenhuma execução de aceitação aprovada para o commit abc1234
   (ADR-0007 §1); falta o parecer do crítico…"). Essa lista é a parte útil da
   resposta — é a receita do que fazer para entrar. Repassá-la como string
   obrigaria cada cliente a separá-la por ponto-e-vírgula para mostrar uma
   lista; então ela é separada UMA vez, aqui, em `MergeRefusal.missing`.

   E a recusa vira RESPOSTA, não exceção: para o agente que pede a entrada na
   fila, "ainda não, falta isto" é trabalho a fazer, não falha (ADR-0007 §2). O
   REST devolve a mesma coisa com 412, porque lá o status faz parte da resposta.

2. **O quadro de entrega numa chamada.** PRs e diretrizes do projeto são duas
   RPCs no núcleo e uma tela só; `get_board` pede as duas em paralelo.

3. **`pending_reviews`.** O núcleo devolve os revisores com status cru; quem
   ordena a caixa de atenção quer o NÚMERO de quem ainda não se pronunciou.
   Contar em cada cliente é a mesma regra escrita três vezes.

Nada disto decide sobre o verde: o verde é derivado das execuções de
verificação, no núcleo, sobre um commit específico (ADR-0007/ADR-0008). A borda
não recalcula nem cacheia essa conclusão — o verde de ontem não é o verde de
agora, e um segundo juiz do mesmo fato é como as duas pontas passam a discordar.
"""

import asyncio

import grpc
from google.protobuf import struct_pb2
from google.protobuf.json_format import MessageToDict
from grpc.aio import AioRpcError
from pydantic import BaseModel, Field

from app.coreclient import stubs
from app.coreclient.client import core
from app.coreclient.convert import call_context_from
from app.coreclient.gen.dop.v1 import common_pb2, delivery_pb2
from app.platform.context import auth_ctx
from app.platform.logging.decorator import log
from app.platform.security.decorator import account_scoped, require_role
from app.settings import settings

_ESTADO_POR_ENUM: dict[int, str] = {
    delivery_pb2.MergeQueueEntry.STATE_QUEUED: "queued",
    delivery_pb2.MergeQueueEntry.STATE_REBASING: "rebasing",
    delivery_pb2.MergeQueueEntry.STATE_VERIFYING: "verifying",
    delivery_pb2.MergeQueueEntry.STATE_MERGED: "merged",
    delivery_pb2.MergeQueueEntry.STATE_CONFLICT: "conflict",
}

_DIRETRIZ_POR_ENUM: dict[int, str] = {
    delivery_pb2.Directive.KIND_CHERRY_PICK: "cherry_pick",
    delivery_pb2.Directive.KIND_MERGE_ORDER: "merge_order",
    delivery_pb2.Directive.KIND_FILE_PARTITION: "file_partition",
    delivery_pb2.Directive.KIND_CROSS_VERIFY: "cross_verify",
}

# Como o núcleo monta a frase da recusa (internal/domain/delivery/service.go):
# "<razão>: <item>; <item>". O primeiro ": " separa a razão dos itens.
_SEP_RAZAO = ": "
_SEP_ITEM = "; "

# Revisor que ainda não se pronunciou. O vocabulário é do provedor de git e
# viaja cru (dop.v1 usa string aqui); a borda só sabe reconhecer o pendente.
_REVISAO_PENDENTE = "pending"


def _deadline() -> float:
    return settings.core_deadline_s


def _idempotency(chave: str = "") -> str:
    return chave or core.idempotency_key()


# ── modelos da borda ────────────────────────────────────────────────────────


class Reviewer(BaseModel):
    name: str = ""
    initials: str = ""
    status: str = ""  # approved | rejected | pending, do provedor


class PullRequest(BaseModel):
    id: str
    demand_id: str = ""
    repo: str = ""
    source_branch: str = ""
    target_branch: str = ""
    url: str = ""
    merged: bool = False
    has_conflict: bool = False
    reviewers: list[Reviewer] = Field(default_factory=list)
    pending_reviews: int = 0


class MergeQueueEntry(BaseModel):
    id: str
    repo_id: str = ""
    demand_id: str = ""
    position: int = 0
    state: str = ""
    overlapping_files: list[str] = Field(default_factory=list)


class Directive(BaseModel):
    id: str
    project_id: str = ""
    kind: str = ""
    payload: dict = Field(default_factory=dict)
    decided_by_id: str = ""
    decided_by_name: str = ""


class MergeRefusal(BaseModel):
    """A recusa da fila, desmontada — ver o item 1 do docstring do módulo."""

    reason: str = ""
    missing: list[str] = Field(default_factory=list)


class MergeAttempt(BaseModel):
    """Entrou na fila, ou não entrou e aqui está o porquê.

    Exatamente um dos dois vem preenchido. Ausente ≠ zerado: uma entrada zerada
    e uma recusa são a diferença entre estar e não estar na fila.
    """

    entry: MergeQueueEntry | None = None
    refusal: MergeRefusal | None = None


class DeliveryBoard(BaseModel):
    pull_requests: list[PullRequest] = Field(default_factory=list)
    directives: list[Directive] = Field(default_factory=list)


class NewMergeEntry(BaseModel):
    demand_id: str = Field(min_length=1)


class DirectiveDecision(BaseModel):
    """A decisão é payload livre: o formato de cada tipo de diretriz é do
    techlead (ADR-0015), e tipar aqui congelaria o que ainda está sendo
    descoberto."""

    decision: dict = Field(default_factory=dict)


# ── tradução do núcleo para a borda ─────────────────────────────────────────


def _pull_request(pr: delivery_pb2.PullRequest) -> PullRequest:
    revisores = [
        Reviewer(name=r.name, initials=r.initials, status=r.status) for r in pr.reviewers
    ]
    return PullRequest(
        id=pr.id,
        demand_id=pr.demand.id,
        repo=pr.repo,
        source_branch=pr.source_branch,
        target_branch=pr.target_branch,
        url=pr.url,
        merged=pr.merged,
        has_conflict=pr.has_conflict,
        reviewers=revisores,
        pending_reviews=sum(1 for r in revisores if r.status == _REVISAO_PENDENTE),
    )


def _entry(e: delivery_pb2.MergeQueueEntry) -> MergeQueueEntry:
    return MergeQueueEntry(
        id=e.id,
        repo_id=e.repo_id,
        demand_id=e.demand.id,
        position=e.position,
        state=_ESTADO_POR_ENUM.get(e.state, ""),
        overlapping_files=list(e.overlapping_files),
    )


def _directive(d: delivery_pb2.Directive) -> Directive:
    # MessageToDict converte a árvore inteira para tipos Python; iterar o Struct
    # na mão devolveria submensagens do protobuf que o JSON do REST não escreve.
    payload = MessageToDict(d.payload) if d.HasField("payload") else {}
    return Directive(
        id=d.id,
        project_id=d.project.id,
        kind=_DIRETRIZ_POR_ENUM.get(d.kind, ""),
        payload=payload,
        decided_by_id=d.decided_by.id,
        decided_by_name=d.decided_by.name,
    )


def _recusa(detalhe: str) -> MergeRefusal:
    """Desmonta a frase da recusa do núcleo em razão + lista do que falta.

    Tolerante: recusa sem lista (PR já mergeado, demanda sem PR aberto) vira
    razão sozinha, e não uma lista inventada. O texto NUNCA é reescrito — o que
    o núcleo diz é o que o dev lê, porque é ele que sabe o que aconteceu.
    """
    razao, sep, cauda = detalhe.strip().partition(_SEP_RAZAO)
    if not sep:
        return MergeRefusal(reason=detalhe.strip())
    itens = [i.strip() for i in cauda.split(_SEP_ITEM) if i.strip()]
    return MergeRefusal(reason=razao.strip(), missing=itens)


# ── casos de uso ────────────────────────────────────────────────────────────


@log
@account_scoped
async def list_pull_requests(demand_id: str = "", project_id: str = "") -> list[PullRequest]:
    """PRs de uma demanda (tela da demanda) ou de um projeto (tela de entrega)."""
    ctx = auth_ctx.get()
    pedido = delivery_pb2.ListPullRequestsRequest(
        ctx=call_context_from(ctx), demand_id=demand_id
    )
    if project_id:
        pedido.project.id = project_id
    resp = await stubs.delivery_stub().ListPullRequests(
        pedido, metadata=core.metadata(), timeout=_deadline()
    )
    return [_pull_request(pr) for pr in resp.pull_requests]


@log
@account_scoped
async def list_directives(project_id: str) -> list[Directive]:
    ctx = auth_ctx.get()
    resp = await stubs.delivery_stub().ListDirectives(
        delivery_pb2.ListDirectivesRequest(
            ctx=call_context_from(ctx), project=common_pb2.ProjectRef(id=project_id)
        ),
        metadata=core.metadata(),
        timeout=_deadline(),
    )
    return [_directive(d) for d in resp.directives]


@log
@account_scoped
async def get_board(project_id: str) -> DeliveryBoard:
    """A tela de entrega do projeto: PRs e diretrizes, em paralelo.

    Uma não depende da outra, então somar as latências seria transformar a
    agregação em custo. `gather` propaga a primeira falha: meia tela sem dizer
    que está pela metade é pior que erro.
    """
    prs, diretrizes = await asyncio.gather(
        list_pull_requests(project_id=project_id), list_directives(project_id)
    )
    return DeliveryBoard(pull_requests=prs, directives=diretrizes)


@log
@account_scoped
async def get_merge_queue(repo_id: str) -> list[MergeQueueEntry]:
    """A fila de UM repositório — não existe "a fila" no singular (ADR-0008).

    É o repositório que serializa: dois PRs em repositórios diferentes não
    invalidam um ao outro, e uma fila global seria uma serialização inventada.
    """
    ctx = auth_ctx.get()
    resp = await stubs.delivery_stub().GetMergeQueue(
        delivery_pb2.GetMergeQueueRequest(ctx=call_context_from(ctx), repo_id=repo_id),
        metadata=core.metadata(),
        timeout=_deadline(),
    )
    return [_entry(e) for e in resp.entries]


@log
@account_scoped
@require_role("owner", "admin", "developer")
async def enqueue_merge(
    repo_id: str, body: NewMergeEntry, idempotency_key: str = ""
) -> MergeAttempt:
    """Pede entrada na fila do repositório — e devolve a recusa POR EXTENSO.

    FAILED_PRECONDITION aqui não é falha de quem chamou nem do sistema: é o
    núcleo dizendo "o commit ainda não está verde, e eis o que falta". Vira
    resposta com a lista intacta. Qualquer OUTRO status segue subindo para os
    tradutores de sempre (`grpc_exception_handler` no REST, `ErrorInterceptor`
    no gRPC) — inclusive porque só eles sabem redigir 5xx sem vazar detalhe do
    núcleo. Um `except` largo aqui engoliria NOT_FOUND como se fosse recusa.
    """
    ctx = auth_ctx.get()
    try:
        e = await stubs.delivery_stub().EnqueueMerge(
            delivery_pb2.EnqueueMergeRequest(
                ctx=call_context_from(ctx),
                repo_id=repo_id,
                demand_id=body.demand_id,
                idempotency_key=_idempotency(idempotency_key),
            ),
            metadata=core.metadata(),
            timeout=_deadline(),
        )
    except AioRpcError as exc:
        if exc.code() is not grpc.StatusCode.FAILED_PRECONDITION:
            raise
        return MergeAttempt(refusal=_recusa(exc.details() or ""))
    return MergeAttempt(entry=_entry(e))


@log
@account_scoped
@require_role("owner", "admin", "developer")
async def decide_directive(
    directive_id: str, body: DirectiveDecision, idempotency_key: str = ""
) -> Directive:
    """A decisão de coordenação é do DEV (ADR-0015) — o techlead recomenda."""
    ctx = auth_ctx.get()
    decisao = struct_pb2.Struct()
    decisao.update(body.decision)
    d = await stubs.delivery_stub().DecideDirective(
        delivery_pb2.DecideDirectiveRequest(
            ctx=call_context_from(ctx),
            directive_id=directive_id,
            decision=decisao,
            idempotency_key=_idempotency(idempotency_key),
        ),
        metadata=core.metadata(),
        timeout=_deadline(),
    )
    return _directive(d)
