"""Rotas de custo — tradução HTTP dos casos de uso, nada mais.

Zero decisão aqui, autorização inclusa: os decorators estão no caso de uso, e é
por isso que a porta gRPC nasce com as mesmas regras.
"""

from fastapi import APIRouter, Header, Query

from app.usecases import cost as uc
from app.usecases.cost import (
    BudgetView,
    CostSummary,
    Money,
    NewBudget,
    NewUsage,
    RecordUsageOutcome,
    RoutingDecision,
    UsageEventSummary,
)

router = APIRouter(prefix="/api/v1/cost", tags=["custo"])

__all__ = [
    "BudgetView",
    "CostSummary",
    "Money",
    "NewBudget",
    "NewUsage",
    "RecordUsageOutcome",
    "RoutingDecision",
    "UsageEventSummary",
    "router",
]


@router.get("/routing", response_model=RoutingDecision)
async def route_model(
    task_kind: str = Query(min_length=1), demand_id: str = ""
) -> RoutingDecision:
    """Qual modelo e qual effort para este tipo de trabalho — e POR QUÊ.

    `reason` vem inteiro, com a proveniência da política na frente
    ("ADR-0011 §3 (rascunho — calibrar com telemetria, P-7): …"). Não é ruído:
    é o que permite auditar a escolha e recalibrar a tabela. Cliente que
    truncar essa string está jogando fora a única parte auditável da resposta.

    É `GET` porque a decisão é PURA — mesma entrada, mesma saída, sem efeito.
    """
    return await uc.route_model(task_kind, demand_id)


@router.get("/budget", response_model=BudgetView)
async def get_budget(scope: str = "", scope_id: str = "") -> BudgetView:
    """Orçamento do escopo. Vazio = a conta ativa.

    `remaining: null` significa escopo SEM TETO — não "acabou o dinheiro".
    Valores em micros (10⁻⁶ da moeda), inteiros, com a moeda ao lado.
    """
    return await uc.get_budget(scope, scope_id)


@router.put("/budget", response_model=BudgetView)
async def set_budget(body: NewBudget) -> BudgetView:
    """Define o teto (owner/admin — orçamento é governança).

    `PUT` porque grava valor ABSOLUTO: repetir a chamada grava o mesmo teto, e
    é por isso que esta escrita não carrega `Idempotency-Key`.
    """
    return await uc.set_budget(body)


@router.post("/usage", response_model=RecordUsageOutcome)
async def record_usage(
    body: NewUsage,
    idempotency_key: str = Header(default="", alias="Idempotency-Key"),
) -> RecordUsageOutcome:
    """Registra consumo de modelo.

    **Orçamento estourado responde 200, não 4xx.** O corte da ADR-0011 §2 é
    suave: a demanda pausa e pergunta, e o consumo continua sendo medido. A
    resposta traz `budget_exceeded`, o `notice` pronto para a caixa de atenção
    e os orçamentos estourados com os números da decisão.
    """
    return await uc.record_usage(body, idempotency_key)


@router.get("/summary", response_model=CostSummary)
async def summarize_cost(scope: str = "", scope_id: str = "") -> CostSummary:
    """Total do mês corrente, taxa de acerto de cache e os últimos consumos."""
    return await uc.summarize_cost(scope, scope_id)
