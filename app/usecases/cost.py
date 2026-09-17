"""LLM cost use cases — measurement, budget and routing (ADR-0008).

The same discipline as `identity` and `hierarchy`: the rule lives here, the
router and the servicer translate. See `app/usecases/identity.py`'s docstring
for why the decorators live in the use case and not in the adapter.

Three hard rules of this module:

**1. Money is an `int` in MICROS, with the currency alongside — never a
`float`.** There is no `/ 1_000_000` in this file and there must not come to be
one. A 64-bit float does not represent 0.1 exactly; summing a thousand agent
calls in float is how the cent disappears, slowly, in the way that only shows up
at the end-of-month reconciliation. The arithmetic that exists here
(`remaining`) is integer to integer. The one that formats is the screen, which
knows the locale.

**2. A blown budget is NOT an error.** ADR-0008 §2 chose a SOFT cut: the demand
pauses and asks (the attention box), it never dies halfway nor keeps burning.
Returning a 402/RESOURCE_EXHAUSTED here would be the hard cut the ADR refused —
and, worse, it would erase the measurement just when it matters most. So
`record_usage` answers OK, with the readable notice and the blown budgets.

**3. The routing's justification travels whole.** See `route_model`.
"""

from datetime import UTC, datetime

from pydantic import BaseModel, Field

from app.coreclient import stubs
from app.coreclient.client import core
from app.coreclient.gen.dop.v1 import common_pb2, cost_pb2
from app.platform.logging.decorator import log
from app.platform.security.decorator import account_scoped, require_role
from app.settings import settings

# The core's scope vocabulary (cost.Scope). Validating at the edge avoids
# spending a round trip to the core to be told "unknown scope".
_SCOPES = "^(account|demand)$"


def _deadline() -> float:
    return settings.core_deadline_s


def _idempotency(key: str = "") -> str:
    return key or core.idempotency_key()


# ── the edge's models ───────────────────────────────────────────────────────


class Money(BaseModel):
    """A monetary value: an integer in micros (10⁻⁶ of the currency) PLUS the currency.

    The two together, always. A number with no currency is a number somebody will
    add to another currency one day, and nobody will notice until the invoice.
    """

    currency: str = ""
    amount_micros: int = 0


class BudgetView(BaseModel):
    scope: str
    scope_id: str = ""
    limit: Money
    spent: Money
    # None = a scope with NO CEILING (a zero limit, ADR-0008). Zero would mean
    # "the money ran out", which is the opposite — the same absent ≠ zeroed
    # discipline `hierarchy.ProjectSummary.task_manager` applies.
    remaining: Money | None = None


class NewBudget(BaseModel):
    scope: str = Field(default="account", pattern=_SCOPES)
    scope_id: str = ""
    # 0 = no ceiling. The core refuses a negative; the edge refuses first.
    limit_micros: int = Field(default=0, ge=0)


class RoutingDecision(BaseModel):
    task_kind: str
    model: str
    effort: str
    # The justification WITH the provenance, whole. See `route_model`.
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
    # None = the core did not date the event. It does not become epoch zero:
    # "1970" on a cost screen looks like an ancient event, not an undated one.
    at: datetime | None = None


class NewUsage(BaseModel):
    """Model consumption to record.

    `cost_micros` comes from whoever called the model because that is where the
    call's price is known; the sum is not recomputed at the edge.
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
    # A ready-made sentence for the attention box; empty when nothing was blown.
    notice: str = ""
    # The scopes consulted when something was blown. Empty in the normal case —
    # no round trip to the core is spent drawing what did not happen.
    budgets: list[BudgetView] = Field(default_factory=list)


class CostSummary(BaseModel):
    total: Money
    # A ratio, not money: here `float` is the right type.
    cache_hit_ratio: float = 0.0
    recent: list[UsageEventSummary] = Field(default_factory=list)


# ── translating the core into the edge ──────────────────────────────────────


def _money(m: common_pb2.Money) -> Money:
    return Money(currency=m.currency, amount_micros=m.amount_micros)


def _budget_view(b: cost_pb2.Budget) -> BudgetView:
    """The core's budget → the screen's view.

    The currency comes from the `dop.v1.Budget.currency` FIELD, which the core
    now carries (P-19). Before, it was read with `getattr` because the field did
    not exist; the workaround served, but a workaround that stays becomes a
    copied example — and `getattr` over protobuf hides a typo in a field name,
    which is exactly what a direct read reports at once.

    What did NOT change is the discipline: an empty currency still means "the
    core did not say", and the edge still does not invent "USD" to fill the hole
    — the assertion would be right until the first invoice in another currency.
    """
    currency = b.currency
    limit = Money(currency=currency, amount_micros=b.limit_micros)
    spent = Money(currency=currency, amount_micros=b.spent_micros)

    # With no ceiling (a zero limit) there is no remainder to show: absent, not
    # zero.
    remaining = None
    if b.limit_micros > 0:
        # INTEGER arithmetic, and never negative — the same rule as the core's
        # (cost.Budget.Remaining): whoever reads this number wants to know how
        # much can still be spent, and "minus twenty" does not answer that
        # question. The overrun stays visible by comparing `spent` with `limit`.
        remaining = Money(currency=currency, amount_micros=max(b.limit_micros - b.spent_micros, 0))
    return BudgetView(
        scope=b.scope, scope_id=b.scope_id, limit=limit, spent=spent, remaining=remaining
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
        # HasField because a timestamp is a MESSAGE field: absent and zeroed are
        # different things, and protobuf's zero is 1970.
        at=u.at.ToDatetime(tzinfo=UTC) if u.HasField("at") else None,
    )


def _overrun_notice(scopes: list[BudgetView]) -> str:
    """The sentence the attention box shows when the budget is blown.

    Written here, once, and not in each client: three clients writing the same
    explanation is how two of them explain it wrong — and what is at stake is
    the user understanding that the demand PAUSED, not that it died.

    Pending: this is still English prose composed at the edge. dop-core already
    carries a stable translation code plus params next to its developer message;
    when the BFF propagates those, this sentence becomes a key the cockpit
    localizes, and the text here goes back to being only the developer's.
    """
    targets = ", ".join(f"{b.scope}:{b.scope_id}" for b in scopes) or "the active account"
    return (
        f"Budget exceeded ({targets}). The demand PAUSES and becomes an item in "
        "the attention box (ADR-0008 §2): it is not cut off halfway nor does it "
        "keep burning. A human decides — raise the ceiling, cut scope or close "
        "it. Consumption keeps being measured meanwhile."
    )


# ── use cases ───────────────────────────────────────────────────────────────


@log
@account_scoped
async def route_model(task_kind: str, demand_id: str = "") -> RoutingDecision:
    """The task → (model, effort) decision, WITH the justification and the provenance.

    `reason` arrives from the core already prefixed by the policy's provenance —
    "ADR-0008 §3 (draft — calibrate with telemetry, P-7): …" — and crosses
    WHOLE. It looks verbose and that is exactly its value:

      - it is what makes auditing possible ("why did this demand run on the
        expensive model?");
      - it is what makes recalibrating possible: a table row can only be
        challenged if its reason is written down;
      - and it is what warns, in every decision, that the policy is still a
        draft.

    Nor does the edge split that string into "provenance" and "reason" to look
    tidier: separating them would require parsing the core's text, and the parser
    would be a second dictionary — which ages separately and one day disagrees
    with the first.
    """
    d = await stubs.cost_stub().RouteModel(
        cost_pb2.RouteModelRequest(task_kind=task_kind, demand_id=demand_id),
        metadata=core.metadata(),
        timeout=_deadline(),
    )
    return RoutingDecision(task_kind=d.task_kind, model=d.model, effort=d.effort, reason=d.reason)


@log
@account_scoped
async def get_budget(scope: str = "", scope_id: str = "") -> BudgetView:
    """One scope's budget. An empty scope = the active account.

    A scope with no ceiling set returns a zero limit with the real spend: the
    absence of a budget is an answer, not an error.
    """
    b = await stubs.cost_stub().GetBudget(
        cost_pb2.GetBudgetRequest(scope=scope, scope_id=scope_id),
        metadata=core.metadata(),
        timeout=_deadline(),
    )
    return _budget_view(b)


@log
@account_scoped
@require_role("owner", "admin")
async def set_budget(body: NewBudget) -> BudgetView:
    """Sets the scope's ceiling, preserving the accumulated spend.

    A role is required because a budget is GOVERNANCE (ADR-0008): the one who
    spends is not the one who decides how much may be spent. Lowering the ceiling
    below the current spend is allowed on purpose — whoever finds a demand
    burning money has to close the tap now, and the lowering is an overrun like
    any other, which pauses down the usual path.

    It carries no idempotency key: `SetBudget` writes an ABSOLUTE value, not an
    increment, so repeating the call writes the same ceiling. The core's contract
    does not receive one either — announcing a field here only to discard it
    would be promising a guarantee the edge does not deliver.
    """
    b = await stubs.cost_stub().SetBudget(
        cost_pb2.SetBudgetRequest(
            budget=cost_pb2.Budget(
                scope=body.scope,
                scope_id=body.scope_id,
                limit_micros=body.limit_micros,
                # `spent_micros` is not sent: the accumulated value is the
                # system's. Sending the client's would let anyone zero the spend
                # by asking.
            ),
        ),
        metadata=core.metadata(),
        timeout=_deadline(),
    )
    return _budget_view(b)


@log
@account_scoped
async def record_usage(body: NewUsage, idempotency_key: str = "") -> RecordUsageOutcome:
    """Records model consumption — and, if it blew the budget, explains what that means.

    The overrun does NOT become an error (ADR-0008 §2, a soft cut). It becomes an
    OK response with `budget_exceeded`, the attention box's sentence and the
    blown budgets — which are the numbers with which the human decides. A bare
    RESOURCE_EXHAUSTED here would have three defects at once: it would contradict
    the ADR, it would make the caller think the consumption was NOT recorded (it
    was), and it would force every client to reinvent the explanation.

    The extra round trip to the core to fetch the budgets only happens on an
    overrun: the normal case is still a single call.
    """
    stub = stubs.cost_stub()
    usage = cost_pb2.UsageEvent(
        thread_id=body.thread_id,
        model=body.model,
        input_tokens=body.input_tokens,
        output_tokens=body.output_tokens,
        cache_read_tokens=body.cache_read_tokens,
        cache_creation_tokens=body.cache_creation_tokens,
        cost=common_pb2.Money(currency=body.currency, amount_micros=body.cost_micros),
    )
    if body.demand_id:
        usage.demand.CopyFrom(common_pb2.DemandRef(id=body.demand_id))
    resp = await stub.RecordUsage(
        cost_pb2.RecordUsageRequest(
            usage=usage,
            # Mandatory in the core, and for a different reason than usual: a
            # duplicate here collides with nothing, it would come in as
            # legitimate consumption and the budget would become fiction.
            idempotency_key=_idempotency(idempotency_key),
        ),
        metadata=core.metadata(),
        timeout=_deadline(),
    )
    if not resp.budget_exceeded:
        return RecordUsageOutcome(recorded=resp.recorded)

    # Which scopes: the demand's (when there is a demand) and the account's.
    # They are the two ceilings that may have been blown, and the screen has to
    # show WHICH.
    wanted = [("account", "")]
    if body.demand_id:
        wanted.insert(0, ("demand", body.demand_id))
    scopes = [
        _budget_view(
            await stub.GetBudget(
                cost_pb2.GetBudgetRequest(scope=scope, scope_id=target),
                metadata=core.metadata(),
                timeout=_deadline(),
            )
        )
        for scope, target in wanted
    ]
    return RecordUsageOutcome(
        recorded=resp.recorded,
        budget_exceeded=True,
        notice=_overrun_notice(scopes),
        budgets=scopes,
    )


@log
@account_scoped
async def summarize_cost(scope: str = "", scope_id: str = "") -> CostSummary:
    """The period's total, the cache hit ratio and the latest consumption.

    The period is the core's default (the current month, the billing cycle's
    window): the core's contract does not take a start and an end yet, and the
    edge does not invent a window of its own — two windows for the same total is
    how two screens start showing different numbers.
    """
    resp = await stubs.cost_stub().SummarizeCost(
        cost_pb2.SummarizeCostRequest(scope=scope, scope_id=scope_id),
        metadata=core.metadata(),
        timeout=_deadline(),
    )
    return CostSummary(
        total=_money(resp.total),
        cache_hit_ratio=resp.cache_hit_ratio,
        recent=[_usage(u) for u in resp.recent],
    )
