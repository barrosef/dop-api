"""Casos de uso de custo de LLM — medição, orçamento e roteamento (ADR-0011).

Mesma disciplina de `identity` e `hierarchy`: a regra vive aqui, router e
servicer traduzem. Ver o docstring de `app/usecases/identity.py` para o porquê
dos decorators morarem no caso de uso e não no adaptador.

Três regras duras deste módulo:

**1. Dinheiro é `int` em MICROS, com a moeda junto — nunca `float`.** Não há um
`/ 1_000_000` neste arquivo e não deve passar a haver. Um float de 64 bits não
representa 0,1 exatamente; somar mil chamadas de agente em float é como o
centavo some, devagar, do jeito que só aparece na conciliação do fim do mês. A
aritmética que existe aqui (`remaining`) é de inteiro para inteiro. Quem
formata é a tela, que sabe a localidade.

**2. Orçamento estourado NÃO é erro.** A ADR-0011 §2 escolheu corte SUAVE: a
demanda pausa e pergunta (caixa de atenção), nunca morre no meio nem segue
queimando. Devolver 402/RESOURCE_EXHAUSTED aqui seria o corte duro que a ADR
recusou — e, pior, apagaria a medição justo quando ela mais importa. Então
`record_usage` responde OK, com o aviso legível e os orçamentos estourados.

**3. A justificativa do roteamento viaja inteira.** Ver `route_model`.
"""

from datetime import UTC, datetime

from pydantic import BaseModel, Field

from app.coreclient import stubs
from app.coreclient.client import core
from app.coreclient.convert import call_context_from
from app.coreclient.gen.dop.v1 import common_pb2, cost_pb2
from app.platform.context import auth_ctx
from app.platform.logging.decorator import log
from app.platform.security.decorator import account_scoped, require_role
from app.settings import settings

# Vocabulário de escopo do núcleo (cost.Scope). Validar na borda evita gastar
# uma ida ao núcleo para ouvir "escopo desconhecido".
_ESCOPOS = "^(account|demand)$"


def _deadline() -> float:
    return settings.core_deadline_s


def _idempotency(chave: str = "") -> str:
    return chave or core.idempotency_key()


# ── modelos da borda ────────────────────────────────────────────────────────


class Money(BaseModel):
    """Valor monetário: inteiro em micros (10⁻⁶ da moeda) MAIS a moeda.

    Os dois juntos, sempre. Um número sem moeda é um número que alguém vai
    somar com outra moeda um dia, e ninguém vai notar até a fatura.
    """

    currency: str = ""
    amount_micros: int = 0


class BudgetView(BaseModel):
    scope: str
    scope_id: str = ""
    limit: Money
    spent: Money
    # None = escopo SEM TETO (limite zero, ADR-0011). Zero significaria "acabou
    # o dinheiro", que é o oposto — a mesma disciplina de ausente ≠ zerado que
    # `hierarchy.ProjectSummary.task_manager` aplica.
    remaining: Money | None = None


class NewBudget(BaseModel):
    scope: str = Field(default="account", pattern=_ESCOPOS)
    scope_id: str = ""
    # 0 = sem teto. Negativo o núcleo recusa; a borda recusa antes.
    limit_micros: int = Field(default=0, ge=0)


class RoutingDecision(BaseModel):
    task_kind: str
    model: str
    effort: str
    # A justificativa COM a proveniência, inteira. Ver `route_model`.
    reason: str


class UsageEventSummary(BaseModel):
    id: str = ""
    demand_id: str = ""
    thread_id: str = ""
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    cost: Money = Field(default_factory=Money)
    # None = o núcleo não datou o evento. Não vira época zero: "1970" numa tela
    # de custo parece um evento antiquíssimo, não um evento sem data.
    at: datetime | None = None


class NewUsage(BaseModel):
    """Consumo de modelo a registrar.

    `cost_micros` vem de quem chamou o modelo porque é lá que se sabe o preço
    da chamada; a conta não é recalculada na borda.
    """

    model: str = Field(min_length=1)
    demand_id: str = ""
    thread_id: str = ""
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    cache_read_tokens: int = Field(default=0, ge=0)
    cache_creation_tokens: int = Field(default=0, ge=0)
    cost_micros: int = Field(default=0, ge=0)
    currency: str = ""


class RecordUsageOutcome(BaseModel):
    recorded: bool
    budget_exceeded: bool = False
    # Frase pronta para a caixa de atenção; vazia quando não estourou.
    notice: str = ""
    # Os escopos consultados quando houve estouro. Vazia no caso normal — não
    # se gasta ida ao núcleo para desenhar o que não aconteceu.
    budgets: list[BudgetView] = Field(default_factory=list)


class CostSummary(BaseModel):
    total: Money
    # Razão, não dinheiro: aqui `float` é o tipo certo.
    cache_hit_ratio: float = 0.0
    recent: list[UsageEventSummary] = Field(default_factory=list)


# ── tradução do núcleo para a borda ─────────────────────────────────────────


def _money(m: common_pb2.Money) -> Money:
    return Money(currency=m.currency, amount_micros=m.amount_micros)


def _budget_view(b: cost_pb2.Budget) -> BudgetView:
    """Orçamento do núcleo → visão da tela.

    A moeda é lida com `getattr` de propósito: `dop.v1.Budget` ainda NÃO carrega
    moeda (só `Money` carrega), e a borda não inventa "USD" para preencher o
    buraco — moeda vazia significa "o núcleo não disse". No dia em que o
    contrato do núcleo ganhar o campo, esta linha passa a mostrá-lo sozinha.
    """
    moeda = getattr(b, "currency", "")
    limite = Money(currency=moeda, amount_micros=b.limit_micros)
    gasto = Money(currency=moeda, amount_micros=b.spent_micros)

    # Sem teto (limite zero) não há sobra a mostrar: ausente, não zero.
    sobra = None
    if b.limit_micros > 0:
        # Aritmética de INTEIRO, e nunca negativa — a mesma regra do núcleo
        # (cost.Budget.Remaining): quem lê este número quer saber quanto ainda
        # dá para gastar, e "menos vinte" não responde essa pergunta. O estouro
        # continua visível comparando `spent` com `limit`.
        sobra = Money(
            currency=moeda, amount_micros=max(b.limit_micros - b.spent_micros, 0)
        )
    return BudgetView(
        scope=b.scope, scope_id=b.scope_id, limit=limite, spent=gasto, remaining=sobra
    )


def _usage(u: cost_pb2.UsageEvent) -> UsageEventSummary:
    return UsageEventSummary(
        id=u.id,
        demand_id=u.demand.id,
        thread_id=u.thread_id,
        model=u.model,
        input_tokens=u.input_tokens,
        output_tokens=u.output_tokens,
        cache_read_tokens=u.cache_read_tokens,
        cache_creation_tokens=u.cache_creation_tokens,
        cost=_money(u.cost),
        # HasField porque timestamp é campo de MENSAGEM: ausente e zerado são
        # coisas diferentes, e o zero do protobuf é 1970.
        at=u.at.ToDatetime(tzinfo=UTC) if u.HasField("at") else None,
    )


def _aviso_de_estouro(escopos: list[BudgetView]) -> str:
    """A frase que a caixa de atenção mostra quando o orçamento estoura.

    Escrita aqui, uma vez, e não em cada cliente: três clientes redigindo a
    mesma explicação é como dois deles a explicam errado — e o que está em jogo
    é o usuário entender que a demanda PAUSOU, não que ela morreu.
    """
    alvos = ", ".join(f"{b.scope}:{b.scope_id}" for b in escopos) or "conta ativa"
    return (
        f"Orçamento estourado ({alvos}). A demanda PAUSA e vira item da caixa de "
        "atenção (ADR-0011 §2): ela não é cortada no meio nem segue queimando. "
        "Um humano decide — aumentar o teto, cortar escopo ou encerrar. O "
        "consumo continua sendo medido enquanto isso."
    )


# ── casos de uso ────────────────────────────────────────────────────────────


@log
@account_scoped
async def route_model(task_kind: str, demand_id: str = "") -> RoutingDecision:
    """A decisão tarefa → (modelo, effort), COM a justificativa e a proveniência.

    `reason` chega do núcleo já prefixado pela proveniência da política —
    "ADR-0011 §3 (rascunho — calibrar com telemetria, P-7): …" — e atravessa
    INTEIRO. Parece verboso e é exatamente esse o valor:

      - é o que permite auditar ("por que esta demanda rodou no modelo caro?");
      - é o que permite recalibrar: uma linha de tabela só é contestável se o
        motivo dela estiver escrito;
      - e é o que avisa, em toda decisão, que a política ainda é rascunho.

    A borda também não parte essa string em "proveniência" e "motivo" para
    ficar mais arrumada: separá-los exigiria parsear texto do núcleo, e o
    parser seria um segundo dicionário — que envelhece em separado e um dia
    discorda do primeiro.
    """
    ctx = auth_ctx.get()
    d = await stubs.cost_stub().RouteModel(
        cost_pb2.RouteModelRequest(
            ctx=call_context_from(ctx), task_kind=task_kind, demand_id=demand_id
        ),
        metadata=core.metadata(),
        timeout=_deadline(),
    )
    return RoutingDecision(
        task_kind=d.task_kind, model=d.model, effort=d.effort, reason=d.reason
    )


@log
@account_scoped
async def get_budget(scope: str = "", scope_id: str = "") -> BudgetView:
    """Orçamento de um escopo. Escopo vazio = a conta ativa.

    Escopo sem teto definido devolve limite zero com o gasto real: ausência de
    orçamento é resposta, não erro.
    """
    ctx = auth_ctx.get()
    b = await stubs.cost_stub().GetBudget(
        cost_pb2.GetBudgetRequest(
            ctx=call_context_from(ctx), scope=scope, scope_id=scope_id
        ),
        metadata=core.metadata(),
        timeout=_deadline(),
    )
    return _budget_view(b)


@log
@account_scoped
@require_role("owner", "admin")
async def set_budget(body: NewBudget) -> BudgetView:
    """Define o teto do escopo, preservando o acumulado.

    Papel exigido porque orçamento é GOVERNANÇA (ADR-0011): quem gasta não é
    quem decide quanto pode ser gasto. Rebaixar o teto abaixo do gasto corrente
    é permitido de propósito — quem descobre uma demanda queimando dinheiro
    precisa fechar a torneira agora, e o rebaixamento é um estouro como outro
    qualquer, que pausa pelo caminho de sempre.

    Não carrega chave de idempotência: `SetBudget` grava valor ABSOLUTO, não
    incremento, então repetir a chamada grava o mesmo teto. O contrato do
    núcleo também não a recebe — anunciar um campo aqui só para descartá-lo
    seria prometer uma garantia que a borda não entrega.
    """
    ctx = auth_ctx.get()
    b = await stubs.cost_stub().SetBudget(
        cost_pb2.SetBudgetRequest(
            ctx=call_context_from(ctx),
            budget=cost_pb2.Budget(
                scope=body.scope,
                scope_id=body.scope_id,
                limit_micros=body.limit_micros,
                # `spent_micros` não é enviado: o acumulado é do sistema. Mandar
                # o do cliente permitiria zerar o gasto pedindo.
            ),
        ),
        metadata=core.metadata(),
        timeout=_deadline(),
    )
    return _budget_view(b)


@log
@account_scoped
async def record_usage(body: NewUsage, idempotency_key: str = "") -> RecordUsageOutcome:
    """Registra consumo de modelo — e, se estourou, explica o que isso significa.

    O estouro NÃO vira erro (ADR-0011 §2, corte suave). Vira uma resposta OK
    com `budget_exceeded`, a frase da caixa de atenção e os orçamentos
    estourados — que são os números com que o humano decide. Um
    RESOURCE_EXHAUSTED seco aqui teria três defeitos de uma vez: contrariaria a
    ADR, faria o chamador achar que o consumo NÃO foi registrado (ele foi), e
    obrigaria cada cliente a reinventar a explicação.

    A ida extra ao núcleo para buscar os orçamentos só acontece no estouro: o
    caso normal continua sendo uma chamada só.
    """
    ctx = auth_ctx.get()
    stub = stubs.cost_stub()
    usage = cost_pb2.UsageEvent(
        thread_id=body.thread_id,
        model=body.model,
        input_tokens=body.input_tokens,
        output_tokens=body.output_tokens,
        cache_read_tokens=body.cache_read_tokens,
        cache_creation_tokens=body.cache_creation_tokens,
        cost=common_pb2.Money(
            currency=body.currency, amount_micros=body.cost_micros
        ),
    )
    if body.demand_id:
        usage.demand.CopyFrom(common_pb2.DemandRef(id=body.demand_id))
    resp = await stub.RecordUsage(
        cost_pb2.RecordUsageRequest(
            ctx=call_context_from(ctx),
            usage=usage,
            # Obrigatória no núcleo, e por um motivo diferente do usual: uma
            # duplicata aqui não colide com nada, entraria como consumo
            # legítimo e o orçamento viraria ficção.
            idempotency_key=_idempotency(idempotency_key),
        ),
        metadata=core.metadata(),
        timeout=_deadline(),
    )
    if not resp.budget_exceeded:
        return RecordUsageOutcome(recorded=resp.recorded)

    # Quais escopos: o da demanda (quando há demanda) e o da conta. São os dois
    # tetos que podem ter estourado, e a tela precisa mostrar QUAL.
    pedidos = [("account", "")]
    if body.demand_id:
        pedidos.insert(0, ("demand", body.demand_id))
    escopos = [
        _budget_view(
            await stub.GetBudget(
                cost_pb2.GetBudgetRequest(
                    ctx=call_context_from(ctx), scope=escopo, scope_id=alvo
                ),
                metadata=core.metadata(),
                timeout=_deadline(),
            )
        )
        for escopo, alvo in pedidos
    ]
    return RecordUsageOutcome(
        recorded=resp.recorded,
        budget_exceeded=True,
        notice=_aviso_de_estouro(escopos),
        budgets=escopos,
    )


@log
@account_scoped
async def summarize_cost(scope: str = "", scope_id: str = "") -> CostSummary:
    """Total do período, taxa de acerto de cache e os últimos consumos.

    O período é o padrão do núcleo (mês corrente, a janela do ciclo de
    cobrança): o contrato do núcleo ainda não recebe início e fim, e a borda
    não inventa um recorte próprio — dois recortes para o mesmo total é como
    duas telas passam a mostrar números diferentes.
    """
    ctx = auth_ctx.get()
    resp = await stubs.cost_stub().SummarizeCost(
        cost_pb2.SummarizeCostRequest(
            ctx=call_context_from(ctx), scope=scope, scope_id=scope_id
        ),
        metadata=core.metadata(),
        timeout=_deadline(),
    )
    return CostSummary(
        total=_money(resp.total),
        cache_hit_ratio=resp.cache_hit_ratio,
        recent=[_usage(u) for u in resp.recent],
    )
