"""Casos de uso de fluxo de trabalho — o ciclo da demanda, e de onde ele veio.

Mesma disciplina de `identity` e `hierarchy`: a regra vive aqui e só aqui;
`app/routers/workflow.py` traduz HTTP e `app/grpcapi/workflow.py` traduz
protobuf, ambos chamando estas MESMAS funções. Ver o docstring de
`app/usecases/identity.py` para o porquê de os decorators morarem no caso de uso
— autorização presa ao router deixaria a porta gRPC aberta.

O que este módulo entrega ao cliente é a **procedência**: o fluxo efetivo é o
resultado da cadeia `plataforma ◁ conta ◁ workspace ◁ projeto ◁ demanda`
(ADR-0014 §3), e "por que esta demanda seguiu este fluxo?" é a pergunta que
chega ao suporte.

O núcleo devolve isso de duas formas, e a borda usa a certa. `resolved_from` é
uma FRASE, boa para imprimir e péssima para usar:

    "conta ◂ plataforma — etapas: contexto (plataforma), spec (conta), …"

`contributors` e `origins` são os MESMOS fatos, estruturados (P-19). A borda lê
os campos e repassa a frase inteira em `sentence`, para log, mensagem de erro e
conferência. Até a P-20 ela fazia parsing da frase, porque os campos não
existiam — e um contrato que obriga o consumidor a interpretar texto quebra no
dia em que alguém melhora a redação. O parser saiu inteiro, junto com a tabela
de rótulos em português que ele precisava carregar: o vocabulário de escopo já
vem do núcleo igual ao de `owner_scope`, sem tradução no meio.

Este módulo também é o dono do vocabulário de ETAPA (tipo, artefato, portão),
importado por `demand`: em dop.v1 a etapa da demanda usa os enums declarados em
workflow.proto, e repetir as tabelas do outro lado seria criar duas verdades
sobre o que é uma etapa de "spec".
"""

from pydantic import BaseModel, Field

from app.coreclient import stubs
from app.coreclient.client import core
from app.coreclient.convert import call_context_from
from app.coreclient.gen.dop.v1 import workflow_pb2
from app.platform.context import auth_ctx
from app.platform.logging.decorator import log
from app.platform.security.decorator import account_scoped, require_role
from app.settings import settings

# ── vocabulário de etapa: número do proto ↔ nome da borda ───────────────────
# Um lugar só. `demand` importa daqui em vez de repetir, porque a etapa da
# demanda é a instância do que a especificação do fluxo descreve.

_TIPO_POR_ENUM: dict[int, str] = {
    workflow_pb2.STAGE_TYPE_CONTEXT: "context",
    workflow_pb2.STAGE_TYPE_SPEC: "spec",
    workflow_pb2.STAGE_TYPE_PLAN: "plan",
    workflow_pb2.STAGE_TYPE_IMPLEMENTATION: "implementation",
    workflow_pb2.STAGE_TYPE_TEST: "test",
    workflow_pb2.STAGE_TYPE_HUMAN_VALIDATION: "human_validation",
    workflow_pb2.STAGE_TYPE_FINALIZATION: "finalization",
    workflow_pb2.STAGE_TYPE_GENERIC: "generic",
}
_ENUM_POR_TIPO = {nome: valor for valor, nome in _TIPO_POR_ENUM.items()}

_ARTEFATO_POR_ENUM: dict[int, str] = {
    workflow_pb2.ARTIFACT_KIND_DOCUMENT: "document",
    workflow_pb2.ARTIFACT_KIND_SPEC: "spec",
    workflow_pb2.ARTIFACT_KIND_PLAN: "plan",
    workflow_pb2.ARTIFACT_KIND_TEST_PLAN: "test_plan",
    workflow_pb2.ARTIFACT_KIND_DIAGRAM: "diagram",
    workflow_pb2.ARTIFACT_KIND_REPORT: "report",
}
_ENUM_POR_ARTEFATO = {nome: valor for valor, nome in _ARTEFATO_POR_ENUM.items()}

_PORTAO_POR_ENUM: dict[int, str] = {
    workflow_pb2.GATE_NONE: "none",
    workflow_pb2.GATE_HUMAN: "human",
}
_ENUM_POR_PORTAO = {nome: valor for valor, nome in _PORTAO_POR_ENUM.items()}


def tipo_nome(valor: int) -> str:
    return _TIPO_POR_ENUM.get(valor, "")


def tipo_valor(nome: str) -> int:
    return _ENUM_POR_TIPO.get(nome, workflow_pb2.STAGE_TYPE_UNSPECIFIED)


def artefato_nome(valor: int) -> str:
    return _ARTEFATO_POR_ENUM.get(valor, "")


def artefato_valor(nome: str) -> int:
    return _ENUM_POR_ARTEFATO.get(nome, workflow_pb2.ARTIFACT_KIND_UNSPECIFIED)


def portao_nome(valor: int) -> str:
    return _PORTAO_POR_ENUM.get(valor, "")


def portao_valor(nome: str) -> int:
    return _ENUM_POR_PORTAO.get(nome, workflow_pb2.GATE_UNSPECIFIED)


def _deadline() -> float:
    return settings.core_deadline_s


def _idempotency(chave: str = "") -> str:
    return chave or core.idempotency_key()


# ── modelos da borda ────────────────────────────────────────────────────────


class StageSpec(BaseModel):
    key: str = Field(min_length=1)
    name: str = ""
    # Vocabulário FECHADO da plataforma (ADR-0014 §1): tipo novo exige evolução
    # da plataforma. Composição de etapas, essa é livre.
    type: str = "generic"
    artifacts: list[str] = Field(default_factory=list)
    gate: str = "none"
    subtypes: list[str] = Field(default_factory=list)


class Flow(BaseModel):
    id: str = ""
    name: str = ""
    description: str = ""
    version: int = 0
    owner_scope: str = ""
    owner_id: str = ""
    stages: list[StageSpec] = Field(default_factory=list)


class NewFlow(BaseModel):
    """O fluxo entra inteiro — a v1 não tem edição por etapa (ADR-0014 §2)."""

    name: str = Field(min_length=1)
    description: str = ""
    owner_scope: str = Field(min_length=1)
    owner_id: str = ""
    stages: list[StageSpec] = Field(default_factory=list)


class PromotionTarget(BaseModel):
    """Nível para onde o fluxo sobe (demanda → projeto → workspace → conta)."""

    target_scope: str = Field(min_length=1)
    target_id: str = ""


class StageOrigin(BaseModel):
    stage_key: str
    scope: str


class Provenance(BaseModel):
    """O rastro da cadeia, estruturado — ver o docstring do módulo."""

    contributors: list[str] = Field(default_factory=list)
    origins: list[StageOrigin] = Field(default_factory=list)
    sentence: str = ""
    truncated: bool = False


class EffectiveFlow(BaseModel):
    # Ausente quando nenhum nível da cadeia declarou fluxo. Não é um fluxo
    # vazio: é a ausência de fluxo, e a tela precisa distinguir as duas.
    flow: Flow | None = None
    provenance: Provenance = Field(default_factory=Provenance)


class ValidationReport(BaseModel):
    """Relatório, não erro: a tela marca as etapas problemáticas com a lista."""

    valid: bool
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


# ── tradução do núcleo para a borda ─────────────────────────────────────────


def _stage_spec(s: workflow_pb2.StageSpec) -> StageSpec:
    return StageSpec(
        key=s.key,
        name=s.name,
        type=tipo_nome(s.type),
        artifacts=[artefato_nome(a) for a in s.artifacts],
        gate=portao_nome(s.gate),
        subtypes=list(s.subtypes),
    )


def _flow(f: workflow_pb2.Flow) -> Flow:
    return Flow(
        id=f.id,
        name=f.name,
        description=f.description,
        version=f.version,
        owner_scope=f.owner_scope,
        owner_id=f.owner_id,
        stages=[_stage_spec(s) for s in f.stages],
    )


def _flow_para_o_nucleo(body: NewFlow, flow_id: str = "") -> workflow_pb2.Flow:
    return workflow_pb2.Flow(
        id=flow_id,
        name=body.name,
        description=body.description,
        owner_scope=body.owner_scope,
        owner_id=body.owner_id,
        stages=[
            workflow_pb2.StageSpec(
                key=s.key,
                name=s.name,
                type=tipo_valor(s.type),
                artifacts=[artefato_valor(a) for a in s.artifacts],
                gate=portao_valor(s.gate),
                subtypes=s.subtypes,
            )
            for s in body.stages
        ],
    )


def _procedencia(eff: workflow_pb2.EffectiveFlow) -> Provenance:
    """A procedência, lida dos CAMPOS do núcleo — não da frase.

    `contributors` chega como `ScopeRef{scope, id}` e `origins` como
    `StageOrigin{key, scope, scope_id}`, na mesma ordem de `flow.stages`. A
    borda só troca os nomes para os do seu contrato; o vocabulário de escopo já
    é o mesmo de `owner_scope`, então não há tabela de tradução aqui — e é
    justamente essa tabela (com os rótulos em português da frase) que sumiu
    quando o parsing saiu.

    O `id` do contribuinte e o `scope_id` da origem NÃO são repassados: o
    contrato da borda ainda os expõe como escopo nu (`contributors` é uma lista
    de strings). Publicá-los é uma decisão de contrato, não de dívida — está
    registrada no relatório da P-20 como o próximo passo, e o dado já está aqui
    quando ela for tomada.

    `truncated` continua significando o que o contrato promete — "as origens não
    cobrem todas as etapas" —, mas agora é MEDIDO, e não deduzido de um "…" no
    fim da frase. O corte de 12 etapas do núcleo é da frase, e só dela; a lista
    estruturada vem inteira. Medir em vez de deduzir é o que faz este campo
    continuar correto se o núcleo um dia passar a cortar (ou parar de cortar).
    """
    etapas = len(eff.flow.stages) if eff.HasField("flow") else 0
    origens = [
        StageOrigin(stage_key=o.key, scope=o.scope) for o in eff.origins
    ]
    return Provenance(
        contributors=[c.scope for c in eff.contributors],
        origins=origens,
        sentence=eff.resolved_from.strip(),
        truncated=len(origens) < etapas,
    )


# ── casos de uso ────────────────────────────────────────────────────────────


@log
@account_scoped
async def list_flows(owner_scope: str = "", owner_id: str = "") -> list[Flow]:
    """Fluxos visíveis na conta ativa; com escopo, só os daquele nível."""
    ctx = auth_ctx.get()
    resp = await stubs.workflow_stub().ListFlows(
        workflow_pb2.ListFlowsRequest(
            ctx=call_context_from(ctx), owner_scope=owner_scope, owner_id=owner_id
        ),
        metadata=core.metadata(),
        timeout=_deadline(),
    )
    return [_flow(f) for f in resp.flows]


@log
@account_scoped
async def get_flow(flow_id: str) -> Flow:
    ctx = auth_ctx.get()
    f = await stubs.workflow_stub().GetFlow(
        workflow_pb2.GetFlowRequest(ctx=call_context_from(ctx), id=flow_id),
        metadata=core.metadata(),
        timeout=_deadline(),
    )
    return _flow(f)


@log
@account_scoped
async def resolve_flow(scope: str, scope_id: str = "") -> EffectiveFlow:
    """O fluxo efetivo de um nível E o rastro de como se chegou nele.

    A resolução é do NÚCLEO — ele conhece a cadeia inteira e a ordem de
    sobreposição. O que a borda faz é vestir a procedência com o vocabulário do
    contrato de borda: ver `_procedencia` e o docstring do módulo.
    """
    ctx = auth_ctx.get()
    eff = await stubs.workflow_stub().ResolveFlow(
        workflow_pb2.ResolveFlowRequest(
            ctx=call_context_from(ctx), scope=scope, scope_id=scope_id
        ),
        metadata=core.metadata(),
        timeout=_deadline(),
    )
    # HasField: fluxo ausente e fluxo zerado são coisas diferentes — nenhum
    # nível declarou nada, versus um fluxo sem nome e sem etapas.
    fluxo = _flow(eff.flow) if eff.HasField("flow") else None
    return EffectiveFlow(flow=fluxo, provenance=_procedencia(eff))


@log
@account_scoped
@require_role("owner", "admin", "developer")
async def create_flow(body: NewFlow, idempotency_key: str = "") -> Flow:
    """Fluxo é CONHECIMENTO, não credencial: aberto dentro da conta (ADR-0014
    §6). Por isso developer compõe o fluxo do próprio projeto — o que exige
    gestão é PROMOVER, que muda o jeito de trabalhar de quem não pediu."""
    ctx = auth_ctx.get()
    f = await stubs.workflow_stub().CreateFlow(
        workflow_pb2.CreateFlowRequest(
            ctx=call_context_from(ctx),
            flow=_flow_para_o_nucleo(body),
            idempotency_key=_idempotency(idempotency_key),
        ),
        metadata=core.metadata(),
        timeout=_deadline(),
    )
    return _flow(f)


@log
@account_scoped
@require_role("owner", "admin", "developer")
async def update_flow(flow_id: str, body: NewFlow) -> Flow:
    """Alterar gera VERSÃO NOVA no núcleo.

    Sem idempotency_key de propósito: quem versiona é o núcleo, e a chamada não
    cria um segundo agregado se repetir — ela produz outra versão do mesmo, que
    é o efeito pedido. As demandas em andamento seguem na versão que
    congelaram (ADR-0014 §4).
    """
    ctx = auth_ctx.get()
    f = await stubs.workflow_stub().UpdateFlow(
        workflow_pb2.UpdateFlowRequest(
            ctx=call_context_from(ctx), flow=_flow_para_o_nucleo(body, flow_id)
        ),
        metadata=core.metadata(),
        timeout=_deadline(),
    )
    return _flow(f)


@log
@account_scoped
async def validate_flow(body: NewFlow) -> ValidationReport:
    """Ensaio antes de gravar — não altera nada, então não exige papel de escrita."""
    ctx = auth_ctx.get()
    resp = await stubs.workflow_stub().ValidateFlow(
        workflow_pb2.ValidateFlowRequest(
            ctx=call_context_from(ctx), flow=_flow_para_o_nucleo(body)
        ),
        metadata=core.metadata(),
        timeout=_deadline(),
    )
    return ValidationReport(
        valid=resp.valid, errors=list(resp.errors), warnings=list(resp.warnings)
    )


@log
@account_scoped
@require_role("owner", "admin")
async def promote_flow(flow_id: str, body: PromotionTarget) -> Flow:
    """Promover é mudar o processo de quem não pediu — daí exigir gestão.

    ADR-0014 §5 pede `manage` sobre o fluxo; enquanto o núcleo não expuser
    concessão por fluxo ao BFF, owner e admin (que têm manage implícito em todo
    recurso) são a aproximação conservadora: recusa a mais, nunca a menos.
    """
    ctx = auth_ctx.get()
    f = await stubs.workflow_stub().PromoteFlow(
        workflow_pb2.PromoteFlowRequest(
            ctx=call_context_from(ctx),
            flow_id=flow_id,
            target_scope=body.target_scope,
            target_id=body.target_id,
        ),
        metadata=core.metadata(),
        timeout=_deadline(),
    )
    return _flow(f)
