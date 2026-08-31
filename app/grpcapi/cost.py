"""Servicer gRPC de custo — adaptador protobuf sobre os casos de uso.

Simétrico a `app/routers/cost.py`: recebe mensagem, chama a MESMA função de
`app/usecases/cost.py`, devolve mensagem. Nenhuma decisão aqui — nem qual
modelo atende qual trabalho, nem o que é estouro.

Dois cuidados que este arquivo não pode perder:

  - **dinheiro é `int64` em micros.** Não há divisão nem `float` neste arquivo,
    e não deve passar a haver: a conversão para a unidade da moeda é trabalho
    da tela, que sabe a localidade;
  - **`remaining` ausente é ausente.** Escopo sem teto não tem sobra a mostrar,
    e um `Money` zerado diria "acabou o dinheiro".
"""

from app.grpcapi.gen.dop.bff.v1 import cost_pb2 as bff
from app.grpcapi.gen.dop.bff.v1 import cost_pb2_grpc as bff_grpc
from app.usecases import cost as uc


def _money(m: uc.Money) -> bff.Money:
    return bff.Money(currency=m.currency, amount_micros=m.amount_micros)


def _budget(b: uc.BudgetView) -> bff.BudgetView:
    msg = bff.BudgetView(
        scope=b.scope,
        scope_id=b.scope_id,
        limit=_money(b.limit),
        spent=_money(b.spent),
    )
    if b.remaining is not None:
        msg.remaining.CopyFrom(_money(b.remaining))
    return msg


def _usage(u: uc.UsageEventSummary) -> bff.UsageEvent:
    msg = bff.UsageEvent(
        id=u.id,
        demand_id=u.demand_id,
        thread_id=u.thread_id,
        model=u.model,
        input_tokens=u.input_tokens,
        output_tokens=u.output_tokens,
        cache_read_tokens=u.cache_read_tokens,
        cache_creation_tokens=u.cache_creation_tokens,
        cost=_money(u.cost),
    )
    # Evento sem data fica sem data: o zero do protobuf é 1970, e 1970 numa
    # tela de custo parece um evento antiquíssimo em vez de um evento sem data.
    if u.at is not None:
        msg.at.FromDatetime(u.at)
    return msg


class CostServicer(bff_grpc.CostServiceServicer):
    async def RouteModel(
        self, request: bff.RouteModelRequest, context
    ) -> bff.RoutingDecision:
        d = await uc.route_model(request.task_kind, request.demand_id)
        return bff.RoutingDecision(
            task_kind=d.task_kind,
            model=d.model,
            effort=d.effort,
            # A justificativa vai INTEIRA, com a proveniência que vem na frente
            # dela. É o que permite auditar e recalibrar; resumir aqui jogaria
            # fora a única parte contestável da resposta.
            reason=d.reason,
        )

    async def GetBudget(self, request: bff.GetBudgetRequest, context) -> bff.BudgetView:
        return _budget(await uc.get_budget(request.scope, request.scope_id))

    async def SetBudget(self, request: bff.SetBudgetRequest, context) -> bff.BudgetView:
        body = uc.NewBudget(
            scope=request.scope or "account",
            scope_id=request.scope_id,
            limit_micros=request.limit_micros,
        )
        return _budget(await uc.set_budget(body))

    async def RecordUsage(
        self, request: bff.RecordUsageRequest, context
    ) -> bff.RecordUsageOutcome:
        u = request.usage
        body = uc.NewUsage(
            model=u.model,
            demand_id=u.demand_id,
            thread_id=u.thread_id,
            input_tokens=u.input_tokens,
            output_tokens=u.output_tokens,
            cache_read_tokens=u.cache_read_tokens,
            cache_creation_tokens=u.cache_creation_tokens,
            cost_micros=u.cost.amount_micros,
            currency=u.cost.currency,
        )
        out = await uc.record_usage(body, request.idempotency_key)
        # Estouro NÃO vira `context.abort`: o corte da ADR-0011 §2 é suave, e um
        # RESOURCE_EXHAUSTED aqui faria o chamador achar que o consumo não foi
        # registrado — ele foi.
        return bff.RecordUsageOutcome(
            recorded=out.recorded,
            budget_exceeded=out.budget_exceeded,
            notice=out.notice,
            budgets=[_budget(b) for b in out.budgets],
        )

    async def SummarizeCost(
        self, request: bff.SummarizeCostRequest, context
    ) -> bff.CostSummary:
        s = await uc.summarize_cost(request.scope, request.scope_id)
        return bff.CostSummary(
            total=_money(s.total),
            cache_hit_ratio=s.cache_hit_ratio,
            recent=[_usage(u) for u in s.recent],
        )
