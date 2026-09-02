"""Cost routes — the use cases' HTTP translation, nothing more.

Zero decisions here, authorization included: the decorators are in the use case,
and that is why the gRPC port is born with the same rules.
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

router = APIRouter(prefix="/api/v1/cost", tags=["cost"])

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
    """Which model and which effort for this kind of work — and WHY.

    `reason` comes whole, with the policy's provenance up front ("ADR-0011 §3
    (draft — calibrate with telemetry, P-7): …"). It is not noise: it is what
    makes auditing the choice and recalibrating the table possible. A client that
    truncates that string is throwing away the only auditable part of the answer.

    It is a `GET` because the decision is PURE — the same input, the same output,
    no effect.
    """
    return await uc.route_model(task_kind, demand_id)


@router.get("/budget", response_model=BudgetView)
async def get_budget(scope: str = "", scope_id: str = "") -> BudgetView:
    """The scope's budget. Empty = the active account.

    `remaining: null` means a scope with NO CEILING — not "the money ran out".
    Values in micros (10⁻⁶ of the currency), integers, with the currency
    alongside.
    """
    return await uc.get_budget(scope, scope_id)


@router.put("/budget", response_model=BudgetView)
async def set_budget(body: NewBudget) -> BudgetView:
    """Sets the ceiling (owner/admin — a budget is governance).

    A `PUT` because it writes an ABSOLUTE value: repeating the call writes the
    same ceiling, and that is why this write carries no `Idempotency-Key`.
    """
    return await uc.set_budget(body)


@router.post("/usage", response_model=RecordUsageOutcome)
async def record_usage(
    body: NewUsage,
    idempotency_key: str = Header(default="", alias="Idempotency-Key"),
) -> RecordUsageOutcome:
    """Records model consumption.

    **A blown budget answers 200, not a 4xx.** ADR-0011 §2's cut is soft: the
    demand pauses and asks, and consumption keeps being measured. The response
    brings `budget_exceeded`, the `notice` ready for the attention box and the
    blown budgets with the decision's numbers.
    """
    return await uc.record_usage(body, idempotency_key)


@router.get("/summary", response_model=CostSummary)
async def summarize_cost(scope: str = "", scope_id: str = "") -> CostSummary:
    """The current month's total, the cache hit ratio and the latest consumption."""
    return await uc.summarize_cost(scope, scope_id)
