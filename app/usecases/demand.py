"""Casos de uso de demanda — a unidade de trabalho do cockpit.

Mesma disciplina de `identity` e `hierarchy`: a regra vive aqui e só aqui;
`app/routers/demand.py` traduz HTTP e `app/grpcapi/demand.py` traduz protobuf,
os dois chamando estas MESMAS funções. Os decorators moram no caso de uso, não
no adaptador — ver o docstring de `app/usecases/identity.py`.

Duas coisas este módulo faz que o núcleo não faz, e é por elas que a borda
existe:

1. **O cockpit numa chamada.** `get_cockpit` pede demanda, threads e achados em
   PARALELO e devolve os três juntos. Em série, a tela montaria em três tempos;
   pedidos por RPCs separadas pelo cliente, o cockpit, o dop-cli e o agente
   fariam cada um a sua orquestração.

2. **Os derivados de "onde a demanda está".** `current_stage_key`, `blocked` e
   `awaiting_decision` são a mesma regrinha de leitura das etapas que cada
   cliente escreveria do seu jeito — e três leituras diferentes é como a lista
   de demandas e a tela da demanda passam a discordar. A condição de
   `awaiting_decision` é a MESMA que o núcleo aceita em `DecideGate`: portão
   humano, etapa começada e não concluída.

O vocabulário de etapa (tipo, artefato, portão) vem de `usecases.workflow`,
como em dop.v1 a demanda importa os enums de workflow.proto: a etapa da demanda
é a instância do que a especificação do fluxo descreve.
"""

import asyncio
from datetime import datetime

from google.protobuf import struct_pb2
from google.protobuf.json_format import MessageToDict
from pydantic import BaseModel, Field

from app.coreclient import stubs
from app.coreclient.client import core
from app.coreclient.convert import call_context_from
from app.coreclient.gen.dop.v1 import common_pb2, demand_pb2, workflow_pb2
from app.platform.context import auth_ctx
from app.platform.logging.decorator import log
from app.platform.security.decorator import account_scoped, require_role
from app.settings import settings
from app.usecases.workflow import artifact_name, gate_name, stage_type_name

# Situação da demanda no NOSSO vocabulário. A do provedor viaja crua, em
# `provider_status`: normalizar as duas no mesmo campo apagaria a diferença
# entre o que a plataforma sabe e o que o quadro do cliente diz.
_STATUS_POR_ENUM: dict[int, str] = {
    demand_pb2.DOP_STATUS_NEW: "new",
    demand_pb2.DOP_STATUS_DOING: "doing",
    demand_pb2.DOP_STATUS_DONE: "done",
    demand_pb2.DOP_STATUS_DELIVERED: "delivered",
}

_ETAPA_POR_ENUM: dict[int, str] = {
    demand_pb2.STAGE_STATUS_PENDING: "pending",
    demand_pb2.STAGE_STATUS_RUNNING: "running",
    demand_pb2.STAGE_STATUS_BLOCKED: "blocked",
    demand_pb2.STAGE_STATUS_DONE: "done",
}
_ENUM_POR_ETAPA = {nome: valor for valor, nome in _ETAPA_POR_ENUM.items()}

_ATOR_POR_ENUM: dict[int, str] = {
    common_pb2.ActorRef.KIND_USER: "user",
    common_pb2.ActorRef.KIND_AGENT: "agent",
    common_pb2.ActorRef.KIND_SUBAGENT: "subagent",
    common_pb2.ActorRef.KIND_SYSTEM: "system",
}

# Etapa que espera GENTE: portão humano, começada e não concluída. É a condição
# que o núcleo exige em DecideGate — repetida aqui como LEITURA (a decisão
# continua sendo dele), para que a tela saiba o que pedir antes de pedir.
_ETAPAS_EM_ABERTO = (demand_pb2.STAGE_STATUS_RUNNING, demand_pb2.STAGE_STATUS_BLOCKED)


def status_etapa_valor(nome: str) -> int:
    return _ENUM_POR_ETAPA.get(nome, demand_pb2.STAGE_STATUS_UNSPECIFIED)


def _deadline() -> float:
    return settings.core_deadline_s


def _idempotency(chave: str = "") -> str:
    return chave or core.idempotency_key()


def _instante(msg, campo: str) -> datetime | None:
    """Timestamp do protobuf, ou None quando o campo não veio.

    HasField, e não `!= 0`: etapa que não começou não tem início, e um zero
    traduzido virava 1970 na tela — data errada é pior que data nenhuma.
    """
    if not msg.HasField(campo):
        return None
    return getattr(msg, campo).ToDatetime()


# ── modelos da borda ────────────────────────────────────────────────────────


class Artifact(BaseModel):
    id: str
    kind: str = ""
    name: str = ""
    # Ponteiro no ObjectStore. O conteúdo NÃO passa por aqui: a borda entrega a
    # referência e quem precisa do bytes vai buscá-lo com URL assinada.
    object_ref: str = ""
    version: int = 0


class Stage(BaseModel):
    key: str
    name: str = ""
    type: str = ""
    status: str = ""
    gate: str = ""
    artifacts: list[Artifact] = Field(default_factory=list)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    awaiting_decision: bool = False


class Demand(BaseModel):
    id: str
    project_id: str = ""
    external_key: str = ""
    title: str = ""
    card_type: str = ""
    provider_status: str = ""
    dop_status: str = ""
    flow_id: str = ""
    flow_version: int = 0
    stages: list[Stage] = Field(default_factory=list)
    # Derivados — ver o docstring do módulo.
    current_stage_key: str = ""
    blocked: bool = False
    awaiting_decision: bool = False


class DemandPage(BaseModel):
    demands: list[Demand] = Field(default_factory=list)
    next_page_token: str = ""


class AgentCard(BaseModel):
    purpose: str = ""
    tools: list[str] = Field(default_factory=list)
    model: str = ""
    effort: str = ""
    budget_micros: int = 0


class Thread(BaseModel):
    id: str
    key: str = ""
    blocked: bool = False
    # Ausente ≠ zerada: thread sem ficha não tem agente atrás; ficha vazia seria
    # um agente sem propósito, sem ferramentas e sem orçamento.
    card: AgentCard | None = None


class Message(BaseModel):
    id: str
    thread_id: str = ""
    author_kind: str = ""
    author_id: str = ""
    author_name: str = ""
    text: str = ""
    at: datetime | None = None


class Finding(BaseModel):
    id: str
    thread_id: str = ""
    title: str = ""
    payload: dict = Field(default_factory=dict)


class DemandCockpit(BaseModel):
    demand: Demand
    threads: list[Thread] = Field(default_factory=list)
    # Lista vazia aqui significa "esta demanda não tem achados", e só isso —
    # ver `get_cockpit` para o porquê de não haver mais uma bandeira ao lado.
    findings: list[Finding] = Field(default_factory=list)


class NewDemand(BaseModel):
    project_id: str = Field(min_length=1)
    # A identidade da demanda lá fora, no quadro do provedor (SUOPT-1315). O
    # fluxo NÃO é escolhido aqui: é resolvido pela cadeia e congelado.
    external_key: str = Field(min_length=1)


class StageTransition(BaseModel):
    """Para onde a etapa vai. O vocabulário é fechado e validado AQUI, no caso
    de uso: assim a recusa de um status inventado vale nos dois transportes —
    422 no REST, INVALID_ARGUMENT no gRPC — sem checagem repetida no adaptador."""

    status: str = Field(pattern="^(pending|running|blocked|done)$")


class GateDecision(BaseModel):
    approved: bool
    comment: str = ""


class NewThread(BaseModel):
    key: str = Field(min_length=1)
    card: AgentCard | None = None


class NewMessage(BaseModel):
    text: str = Field(min_length=1)


class NewFinding(BaseModel):
    thread_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    payload: dict = Field(default_factory=dict)


# ── tradução do núcleo para a borda ─────────────────────────────────────────


def _artifact(a: demand_pb2.Artifact) -> Artifact:
    return Artifact(
        id=a.id,
        kind=artifact_name(a.kind),
        name=a.name,
        object_ref=a.object_ref,
        version=a.version,
    )


def _stage(s: demand_pb2.DemandStage) -> Stage:
    return Stage(
        key=s.key,
        name=s.name,
        type=stage_type_name(s.type),
        status=_ETAPA_POR_ENUM.get(s.status, ""),
        gate=gate_name(s.gate),
        artifacts=[_artifact(a) for a in s.artifacts],
        started_at=_instante(s, "started_at"),
        finished_at=_instante(s, "finished_at"),
        awaiting_decision=_espera_decisao(s),
    )


def _espera_decisao(s: demand_pb2.DemandStage) -> bool:
    return s.gate == workflow_pb2.GATE_HUMAN and s.status in _ETAPAS_EM_ABERTO


def _demand(d: demand_pb2.Demand) -> Demand:
    etapas = [_stage(s) for s in d.stages]
    # A primeira não concluída é "onde a demanda está". Percorrer na ordem do
    # fluxo importa: é a ordem em que o núcleo cobra o progresso.
    atual = next((e for e in etapas if e.status != "done"), None)
    return Demand(
        id=d.id,
        project_id=d.project.id,
        external_key=d.external_key,
        title=d.title,
        card_type=d.card_type,
        provider_status=d.provider_status,
        dop_status=_STATUS_POR_ENUM.get(d.dop_status, ""),
        flow_id=d.flow_id,
        flow_version=d.flow_version,
        stages=etapas,
        current_stage_key=atual.key if atual else "",
        blocked=any(e.status == "blocked" for e in etapas),
        awaiting_decision=any(e.awaiting_decision for e in etapas),
    )


def _thread(t: demand_pb2.Thread) -> Thread:
    ficha = None
    if t.HasField("card"):
        c = t.card
        ficha = AgentCard(
            purpose=c.purpose,
            tools=list(c.tools),
            model=c.model,
            effort=c.effort,
            budget_micros=c.budget_micros,
        )
    return Thread(id=t.id, key=t.key, blocked=t.blocked, card=ficha)


def _message(m: demand_pb2.Message) -> Message:
    return Message(
        id=m.id,
        thread_id=m.thread_id,
        author_kind=_ATOR_POR_ENUM.get(m.author.kind, ""),
        author_id=m.author.id,
        author_name=m.author.name,
        text=m.text,
        at=_instante(m, "at"),
    )


def _finding(f: demand_pb2.Finding) -> Finding:
    # MessageToDict converte a árvore INTEIRA para tipos Python. Iterar o
    # Struct na mão devolveria submensagens do protobuf aninhadas, que o
    # serializador JSON do REST não sabe escrever — e o achado é payload livre,
    # então aninhado é o caso normal, não a exceção.
    payload = MessageToDict(f.payload) if f.HasField("payload") else {}
    return Finding(id=f.id, thread_id=f.thread_id, title=f.title, payload=payload)


# ── casos de uso ────────────────────────────────────────────────────────────


@log
@account_scoped
async def list_demands(
    project_id: str = "", page_size: int = 0, page_token: str = ""
) -> DemandPage:
    ctx = auth_ctx.get()
    pedido = demand_pb2.ListDemandsRequest(
        ctx=call_context_from(ctx),
        page=common_pb2.PageRequest(size=page_size, token=page_token),
    )
    if project_id:
        pedido.project.id = project_id
    resp = await stubs.demand_stub().ListDemands(
        pedido, metadata=core.metadata(), timeout=_deadline()
    )
    return DemandPage(
        demands=[_demand(d) for d in resp.demands], next_page_token=resp.page.next_token
    )


@log
@account_scoped
async def get_demand(demand_id: str) -> Demand:
    ctx = auth_ctx.get()
    d = await stubs.demand_stub().GetDemand(
        demand_pb2.GetDemandRequest(ctx=call_context_from(ctx), id=demand_id),
        metadata=core.metadata(),
        timeout=_deadline(),
    )
    return _demand(d)


@log
@account_scoped
async def list_threads(demand_id: str) -> list[Thread]:
    ctx = auth_ctx.get()
    resp = await stubs.demand_stub().ListThreads(
        demand_pb2.ListThreadsRequest(ctx=call_context_from(ctx), demand_id=demand_id),
        metadata=core.metadata(),
        timeout=_deadline(),
    )
    return [_thread(t) for t in resp.threads]


@log
@account_scoped
async def list_findings(demand_id: str, thread_id: str = "") -> list[Finding]:
    """O quadro de achados da demanda; com `thread_id`, os de uma thread só.

    É o registro durável de cada investigação concluída (ADR-0009) — e é ele
    que impede um agente, ou um humano, de refazer o que outro já terminou.

    Lê por `DemandService.ListFindings`, e NÃO pelo `BuildContextPackage` do
    KnowledgeService: aquele pacote é SELECIONADO por orçamento de tokens
    (mostraria parte dos achados como se fossem todos) e a montagem grava um
    evento de medição — abrir uma tela viraria linha de custo de contexto.

    Sem paginação na borda: a página é a do núcleo. Quadro de achados é para
    LER, não para navegar; se uma demanda tiver mais achados do que a página do
    núcleo comporta, o problema a resolver não é a paginação da tela.
    """
    ctx = auth_ctx.get()
    resp = await stubs.demand_stub().ListFindings(
        demand_pb2.ListFindingsRequest(
            ctx=call_context_from(ctx), demand_id=demand_id, thread_id=thread_id
        ),
        metadata=core.metadata(),
        timeout=_deadline(),
    )
    return [_finding(f) for f in resp.findings]


@log
@account_scoped
async def get_cockpit(demand_id: str) -> DemandCockpit:
    """A tela da demanda inteira: etapas, threads e achados.

    Em PARALELO, não em série: as três chamadas não dependem umas das outras, e
    somar as latências seria transformar a agregação num custo em vez de um
    ganho. `gather` propaga a primeira falha — resposta pela metade sem dizer
    que está pela metade é pior que erro.

    **Por que não existe mais `findings_available`.** O campo nasceu quando o
    contrato do núcleo só tinha `PublishFinding`: a lista vinha vazia e a
    bandeira dizia "não dá para saber", porque "esta demanda não tem achados" e
    "a borda não consegue ler achados" são fatos diferentes e a tela precisava
    distinguir. Com `ListFindings` no contrato (P-19) o segundo fato deixou de
    existir: ou a leitura funciona, e a lista é a resposta, ou ela falha, e o
    `gather` faz a chamada inteira falhar com o status do núcleo. Não sobrou
    estado para a bandeira descrever — ela seria `true` constante, e campo que
    só sabe dizer uma coisa vira ruído que alguém um dia interpreta ao
    contrário.
    """
    demanda, threads, achados = await asyncio.gather(
        get_demand(demand_id), list_threads(demand_id), list_findings(demand_id)
    )
    return DemandCockpit(demand=demanda, threads=threads, findings=achados)


@log
@account_scoped
@require_role("owner", "admin", "developer")
async def start_demand(body: NewDemand, idempotency_key: str = "") -> Demand:
    """Iniciar RESOLVE e CONGELA o fluxo (ADR-0014 §4).

    Por isso é escrita com chave de idempotência e não com um GET-ou-cria: o
    retry do canal sem chave abriria duas demandas para o mesmo card.
    """
    ctx = auth_ctx.get()
    d = await stubs.demand_stub().StartDemand(
        demand_pb2.StartDemandRequest(
            ctx=call_context_from(ctx),
            project=common_pb2.ProjectRef(id=body.project_id),
            external_key=body.external_key,
            idempotency_key=_idempotency(idempotency_key),
        ),
        metadata=core.metadata(),
        timeout=_deadline(),
    )
    return _demand(d)


@log
@account_scoped
@require_role("owner", "admin", "developer")
async def advance_stage(
    demand_id: str, stage_key: str, body: StageTransition, idempotency_key: str = ""
) -> Stage:
    """Move a etapa. Quem valida a transição é o núcleo — a máquina de estados
    é dele, e duplicá-la aqui criaria duas regras para a mesma pergunta."""
    ctx = auth_ctx.get()
    s = await stubs.demand_stub().AdvanceStage(
        demand_pb2.AdvanceStageRequest(
            ctx=call_context_from(ctx),
            demand_id=demand_id,
            stage_key=stage_key,
            status=status_etapa_valor(body.status),
            idempotency_key=_idempotency(idempotency_key),
        ),
        metadata=core.metadata(),
        timeout=_deadline(),
    )
    return _stage(s)


@log
@account_scoped
@require_role("owner", "admin", "developer")
async def decide_gate(
    demand_id: str, stage_key: str, body: GateDecision, idempotency_key: str = ""
) -> Stage:
    """Portão humano: aprovar conclui a etapa, reprovar a bloqueia com o comentário.

    Viewer não decide — e o núcleo ainda recusa qualquer ator que seja agente,
    porque portão humano decidido por agente é o portão não existir.
    """
    ctx = auth_ctx.get()
    s = await stubs.demand_stub().DecideGate(
        demand_pb2.DecideGateRequest(
            ctx=call_context_from(ctx),
            demand_id=demand_id,
            stage_key=stage_key,
            approved=body.approved,
            comment=body.comment,
            idempotency_key=_idempotency(idempotency_key),
        ),
        metadata=core.metadata(),
        timeout=_deadline(),
    )
    return _stage(s)


@log
@account_scoped
@require_role("owner", "admin", "developer")
async def create_thread(demand_id: str, body: NewThread, idempotency_key: str = "") -> Thread:
    """Lança uma thread (subagente) na demanda — ADR-0010."""
    ctx = auth_ctx.get()
    pedido = demand_pb2.CreateThreadRequest(
        ctx=call_context_from(ctx),
        demand_id=demand_id,
        key=body.key,
        idempotency_key=_idempotency(idempotency_key),
    )
    # Só preenche a ficha quando ela veio: mandar uma zerada declararia um
    # agente sem propósito, sem ferramentas e sem orçamento.
    if body.card is not None:
        pedido.card.CopyFrom(
            demand_pb2.AgentCard(
                purpose=body.card.purpose,
                tools=body.card.tools,
                model=body.card.model,
                effort=body.card.effort,
                budget_micros=body.card.budget_micros,
            )
        )
    t = await stubs.demand_stub().CreateThread(
        pedido, metadata=core.metadata(), timeout=_deadline()
    )
    return _thread(t)


@log
@account_scoped
@require_role("owner", "admin", "developer")
async def post_message(
    thread_id: str,
    body: NewMessage,
    idempotency_key: str = "",
    actor_kind: str = "user",
) -> Message:
    """Mensagem na thread. Toda mensagem é evento (ADR-0006).

    `actor_kind` decide a AUTORIA no log. O padrão é humano porque a rota REST
    e o servicer só são chamados por gente; o runtime declara `agent` quando é
    a resposta do modelo. Gravar a fala do agente como fala do humano faria o
    log — que é a verdade da demanda — mentir sobre quem fez o quê, numa
    plataforma cuja premissa inteira é distinguir os dois.
    """
    ctx = auth_ctx.get()
    m = await stubs.demand_stub().PostMessage(
        demand_pb2.PostMessageRequest(
            ctx=call_context_from(ctx, actor_kind),
            thread_id=thread_id,
            text=body.text,
            idempotency_key=_idempotency(idempotency_key),
        ),
        metadata=core.metadata_for(
            user_id=ctx.user_id,
            account_id=ctx.account_id,
            actor_name=ctx.principal.name or ctx.principal.email,
            actor_kind=actor_kind,
        ),
        timeout=_deadline(),
    )
    return _message(m)


@log
@account_scoped
@require_role("owner", "admin", "developer")
async def publish_finding(
    demand_id: str, body: NewFinding, idempotency_key: str = ""
) -> Finding:
    """Publica um achado — o registro durável de uma investigação.

    Concluir uma thread exige publicar o achado: a thread não morre em silêncio
    (spec de conversação §1), e é o achado que entra no contexto dos irmãos.
    """
    ctx = auth_ctx.get()
    payload = struct_pb2.Struct()
    payload.update(body.payload)
    f = await stubs.demand_stub().PublishFinding(
        demand_pb2.PublishFindingRequest(
            ctx=call_context_from(ctx),
            demand_id=demand_id,
            thread_id=body.thread_id,
            title=body.title,
            payload=payload,
            idempotency_key=_idempotency(idempotency_key),
        ),
        metadata=core.metadata(),
        timeout=_deadline(),
    )
    return _finding(f)
