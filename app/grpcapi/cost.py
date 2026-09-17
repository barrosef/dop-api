"""The cost gRPC servicer — a protobuf adapter over the use cases.

Symmetric to `app/routers/cost.py`: it receives a message, calls the SAME
function from `app/usecases/cost.py`, returns a message. No decisions here —
neither which model serves which work, nor what counts as an overrun.

Two cares this file must not lose:

  - **money is an `int64` in micros.** There is no division and no `float` in
    this file, and there must not come to be: converting to the currency's unit
    is the screen's job, which knows the locale;
  - **an absent `remaining` is absent.** A scope with no ceiling has no
    remainder to show, and a zeroed `Money` would say "the money ran out".
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
    # An event with no date stays without one: protobuf's zero is 1970, and 1970
    # on a cost screen looks like an ancient event rather than an undated one.
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
            # The justification goes WHOLE, with the provenance that comes up
            # front. It is what makes auditing and recalibrating possible;
            # summarizing here would throw away the only challengeable part of
            # the answer.
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
        # An overrun does NOT become a `context.abort`: ADR-0008 §2's cut is
        # soft, and a RESOURCE_EXHAUSTED here would make the caller think the
        # consumption was not recorded — it was.
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
