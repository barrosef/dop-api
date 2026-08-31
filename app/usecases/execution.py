"""Casos de uso do substrato de execução — o sandbox da demanda.

Mesma disciplina de `identity` e `hierarchy`: a regra vive aqui, router e
servicer traduzem. Ver o docstring de `app/usecases/identity.py` para o porquê
dos decorators morarem no caso de uso e não no adaptador.

**A regra dura deste módulo: `min_tier` é DECLARADO, nunca presumido.**

Não existe neste arquivo um default para o nível de isolamento, e não deve
passar a existir — nem "namespace porque é o que sempre funciona", nem "o
último que a conta usou". Um default aqui seria a plataforma escolhendo o
isolamento de um código que ela não escreveu, e escolhendo para baixo: quem
pediu microVM e recebeu container não descobre isso pela tela, descobre pelo
incidente. O núcleo recusa o nível não declarado; a borda recusa ANTES, com
mensagem, porque gastar uma ida ao núcleo para ouvir "você não disse" é
desperdício e a mensagem chega igual.

**Destruir é irreversível e leva o workspace junto.** `suspend` mata a execução
e preserva o workspace no PVC (é a operação de economia); `destroy` apaga os
dois, e sandbox destruído não retoma — o caminho é provisionar outro, do zero.
Por isso `destroy_sandbox` devolve confirmação em vez de silêncio.
"""

from datetime import UTC, datetime

from pydantic import BaseModel, Field

from app.coreclient import stubs
from app.coreclient.client import core
from app.coreclient.convert import call_context_from
from app.coreclient.gen.dop.v1 import execution_pb2
from app.platform.context import auth_ctx
from app.platform.logging.decorator import log
from app.platform.security.decorator import account_scoped, require_role
from app.settings import settings

# Nome ↔ enum num lugar só. Os nomes são os do núcleo
# (execution.RequireTier: "hardware, kernel_emulated ou namespace"); a spec do
# substrato escreve `kernel-emulated` com hífen na tabela de ambientes, mas o
# vocabulário que atravessa a fronteira é o do núcleo — dois grafias para o
# mesmo valor é bug de tradução esperando acontecer.
_TIER_POR_NOME = {
    "hardware": execution_pb2.ISOLATION_TIER_HARDWARE,
    "kernel_emulated": execution_pb2.ISOLATION_TIER_KERNEL_EMULATED,
    "namespace": execution_pb2.ISOLATION_TIER_NAMESPACE,
}
_NOME_POR_TIER = {v: k for k, v in _TIER_POR_NOME.items()}

# UNSPECIFIED de propósito FORA do dicionário: ele não é um valor que a borda
# aceita, é a ausência de valor.
_TIERS = "^(hardware|kernel_emulated|namespace)$"

_ESTADO_POR_ENUM = {
    execution_pb2.Sandbox.STATE_PROVISIONING: "provisioning",
    execution_pb2.Sandbox.STATE_ACTIVE: "active",
    execution_pb2.Sandbox.STATE_SUSPENDED: "suspended",
    execution_pb2.Sandbox.STATE_DESTROYED: "destroyed",
}

# Todo papel MENOS viewer. O núcleo recusa viewer em provisionar, suspender,
# retomar e destruir; a borda recusa antes, e recusa igual nas duas portas
# porque o decorator está no caso de uso. Descrever a regra pelos papéis que
# PODEM (e não por quem não pode) é o que o `require_role` oferece — são quatro
# papéis fixos, então a lista é fechada.
_ALTERA_CICLO_DE_VIDA = ("owner", "admin", "developer")


def _deadline() -> float:
    return settings.core_deadline_s


def _idempotency(chave: str = "") -> str:
    return chave or core.idempotency_key()


# ── modelos da borda ────────────────────────────────────────────────────────


class EndpointSummary(BaseModel):
    name: str
    url: str = ""
    port: int = 0
    state: str = ""  # running | stopped


class SandboxSummary(BaseModel):
    id: str
    demand_id: str = ""
    state: str = ""
    # O que o substrato ENTREGOU — o cliente vê o que recebeu. Nunca uma
    # promessa: o núcleo descarta o sandbox se o adaptador entregar outro nível.
    tier: str = ""
    namespace: str = ""
    endpoints: list[EndpointSummary] = Field(default_factory=list)
    # None = sem atividade registrada. Não vira época zero: "ocioso desde 1970"
    # faria a suspensão automática ler errado.
    last_active_at: datetime | None = None


class NewSandbox(BaseModel):
    """Pedido de provisionamento.

    `min_tier` NÃO tem default, e é isso que faz a regra valer: um campo
    opcional com valor padrão seria a presunção voltando pela porta dos fundos.
    Sem ele, a validação recusa aqui mesmo — 422 no REST, INVALID_ARGUMENT no
    gRPC — e o núcleo nem chega a ser chamado.
    """

    demand_id: str = Field(min_length=1)
    min_tier: str = Field(pattern=_TIERS)


class DestroyResult(BaseModel):
    """Confirmação de um ato irreversível.

    Existe como modelo (e não como `None`) porque destruir apaga a execução E o
    workspace: um 204 mudo obrigaria o cliente a deduzir o que aconteceu, e o
    que aconteceu não tem volta.
    """

    destroyed: bool


# ── tradução do núcleo para a borda ─────────────────────────────────────────


def _sandbox(s: execution_pb2.Sandbox) -> SandboxSummary:
    return SandboxSummary(
        id=s.id,
        demand_id=s.demand.id,
        state=_ESTADO_POR_ENUM.get(s.state, ""),
        tier=_NOME_POR_TIER.get(s.tier, ""),
        namespace=s.namespace,
        endpoints=[
            EndpointSummary(name=e.name, url=e.url, port=e.port, state=e.state)
            for e in s.endpoints
        ],
        # HasField porque timestamp é campo de MENSAGEM: ausente ≠ zerado.
        last_active_at=(
            s.last_active_at.ToDatetime(tzinfo=UTC)
            if s.HasField("last_active_at")
            else None
        ),
    )


# ── casos de uso ────────────────────────────────────────────────────────────


@log
@account_scoped
@require_role(*_ALTERA_CICLO_DE_VIDA)
async def provision_sandbox(body: NewSandbox, idempotency_key: str = "") -> SandboxSummary:
    """Cria o sandbox da demanda, no nível de isolamento DECLARADO.

    O `min_tier` desce como o cliente o declarou. A borda não o completa, não o
    rebaixa quando o substrato não oferece o nível pedido (isso é recusa do
    núcleo, com mensagem — degradação silenciosa é o modo de falha que a spec
    do substrato §2 proíbe) e não o promove "por segurança": promover também é
    escolher pelo cliente, e o cliente é quem responde pelo custo.
    """
    ctx = auth_ctx.get()
    s = await stubs.execution_stub().ProvisionSandbox(
        execution_pb2.ProvisionSandboxRequest(
            ctx=call_context_from(ctx),
            demand_id=body.demand_id,
            min_tier=_TIER_POR_NOME[body.min_tier],
            idempotency_key=_idempotency(idempotency_key),
        ),
        metadata=core.metadata(),
        timeout=_deadline(),
    )
    return _sandbox(s)


@log
@account_scoped
async def describe_sandbox(sandbox_id: str) -> SandboxSummary:
    """O sandbox como ele ESTÁ — inclusive o estado de cada endpoint.

    Leitura não exige papel além de conta ativa: ver em que nível de isolamento
    a demanda está rodando é justamente o que a spec quer que seja visível.
    """
    ctx = auth_ctx.get()
    s = await stubs.execution_stub().DescribeSandbox(
        execution_pb2.DescribeSandboxRequest(ctx=call_context_from(ctx), id=sandbox_id),
        metadata=core.metadata(),
        timeout=_deadline(),
    )
    return _sandbox(s)


@log
@account_scoped
@require_role(*_ALTERA_CICLO_DE_VIDA)
async def suspend_sandbox(sandbox_id: str) -> SandboxSummary:
    """Mata a execução e PRESERVA o workspace — a operação de economia.

    Repetir é inócuo: suspender o que já está suspenso devolve o sandbox como
    está, sem tocar no substrato e sem emitir evento.
    """
    ctx = auth_ctx.get()
    s = await stubs.execution_stub().SuspendSandbox(
        execution_pb2.SuspendSandboxRequest(ctx=call_context_from(ctx), id=sandbox_id),
        metadata=core.metadata(),
        timeout=_deadline(),
    )
    return _sandbox(s)


@log
@account_scoped
@require_role(*_ALTERA_CICLO_DE_VIDA)
async def resume_sandbox(sandbox_id: str) -> SandboxSummary:
    """Recria a execução SOBRE o workspace existente.

    Sandbox destruído não retoma, e a recusa vem do núcleo com o motivo escrito
    ("a destruição leva o workspace junto"). A borda repassa: transformar isso
    num "provisiona outro automaticamente" esconderia do dev que ele perdeu o
    workspace.
    """
    ctx = auth_ctx.get()
    s = await stubs.execution_stub().ResumeSandbox(
        execution_pb2.ResumeSandboxRequest(ctx=call_context_from(ctx), id=sandbox_id),
        metadata=core.metadata(),
        timeout=_deadline(),
    )
    return _sandbox(s)


@log
@account_scoped
@require_role(*_ALTERA_CICLO_DE_VIDA)
async def destroy_sandbox(sandbox_id: str) -> DestroyResult:
    """IRREVERSÍVEL: apaga a execução E o workspace da demanda.

    Não é o inverso de `suspend_sandbox`. O que se perde aqui é o worktree das
    branches, o build já feito e tudo que o agente tinha no disco; retomar
    deixa de ser possível e o único caminho passa a ser provisionar um sandbox
    novo, do zero. Quem quer economizar recurso quer `suspend`.

    Idempotente por estado, como no núcleo: destruir o que já foi destruído
    devolve `destroyed=true` sem tocar em nada — quem repete a chamada quer o
    mesmo resultado, e o resultado já está lá.
    """
    ctx = auth_ctx.get()
    resp = await stubs.execution_stub().DestroySandbox(
        execution_pb2.DestroySandboxRequest(ctx=call_context_from(ctx), id=sandbox_id),
        metadata=core.metadata(),
        timeout=_deadline(),
    )
    return DestroyResult(destroyed=resp.destroyed)
