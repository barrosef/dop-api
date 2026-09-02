"""Running an agent turn — a thin call to the core.

**The runtime does NOT live here** (ADR-0023). It used to, and it was moved: the
turn needs the agent provider's credential, which lives in the vault, and the
BFF is the layer exposed to the internet. Giving this layer access to the vault
would mean that compromising it would hand over EVERY account's agent
credentials — and this platform has already had a total authentication bypass
exactly here.

In the core, the credential comes out of the vault and is used in the same
process, crossing no network at all. What is left for the edge is what the edge
should do: authenticate, translate and return.

Live following still goes through the SSE that already exists: the turn's
messages become events in the core and arrive on their own
(`app/usecases/stream.py`). There is no second streaming path, and there must not
be — they would be two sources of truth.
"""

from pydantic import BaseModel, Field

from app.coreclient import stubs
from app.coreclient.client import core
from app.coreclient.gen.dop.v1 import agent_pb2
from app.platform.logging.decorator import log
from app.platform.security.decorator import account_scoped, require_role
from app.settings import settings
from app.usecases import cost as cost_uc


def _deadline() -> float:
    # An agent turn is LONG — a call to a reasoning model takes minutes. The
    # core's deadline (10s by default) would kill every real turn.
    return settings.agent_turn_deadline_s


class RunTurn(BaseModel):
    """A turn to run on a thread."""

    text: str = Field(min_length=1)
    # An OPEN vocabulary (the core handles an unknown kind by falling back to
    # the expensive path and saying so). What does not pass is empty: with no
    # work kind there is no routing.
    task_kind: str = Field(default="implementation", min_length=1)
    # Which agent integration to use. Empty = the account's only one; if there
    # is more than one, the core REFUSES with the list instead of choosing
    # (ADR-0013).
    resource_id: str = ""
    # The OPERATOR's instruction, coming from the attention box. It goes in
    # through the provider's authority channel, never as user text.
    operator_note: str = ""
    max_output_tokens: int = Field(default=8192, ge=256, le=64000)


class TurnUsage(BaseModel):
    """The four parts, DISJOINT, plus the cost in integer micros."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    cost: cost_uc.Money = Field(default_factory=cost_uc.Money)
    # False = the provider does not report cache creation. Zero would assert
    # that nothing was written to the cache, which is another thing.
    cache_creation_known: bool = True
    # False = there is no price table for this model. The cost does NOT become a
    # consolation zero: a budget fed with zeroes is fiction.
    cost_known: bool = True


class RoutingView(BaseModel):
    """The core's decision, with the WHOLE justification (ADR-0011 §3)."""

    task_kind: str = ""
    model: str = ""
    effort: str = ""
    effort_applied: str = ""
    reason: str = ""
    from_agent_card: bool = False


class FindingRef(BaseModel):
    id: str = ""
    title: str = ""


class TurnOutcome(BaseModel):
    demand_id: str
    thread_id: str
    provider: str = ""
    routing: RoutingView = Field(default_factory=RoutingView)
    reply: str = ""
    message_ids: list[str] = Field(default_factory=list)
    concluded: bool = False
    finding: FindingRef | None = None
    usage: TurnUsage = Field(default_factory=TurnUsage)
    context_truncated: bool = False
    paused: bool = False
    notice: str = ""


def _outcome(o: agent_pb2.TurnOutcome) -> TurnOutcome:
    u = o.usage
    finding = None
    # Absent ≠ zeroed: a finding only exists once the thread has concluded.
    if o.HasField("finding"):
        finding = FindingRef(id=o.finding.id, title=o.finding.title)
    return TurnOutcome(
        demand_id=o.demand.id,
        thread_id=o.thread_id,
        provider=o.provider,
        routing=RoutingView(
            task_kind=o.routing.task_kind,
            model=o.routing.model,
            effort=o.routing.effort,
            effort_applied=o.routing.effort_applied,
            reason=o.routing.reason,
            from_agent_card=o.routing.from_agent_card,
        ),
        reply=o.reply,
        message_ids=list(o.message_ids),
        concluded=o.concluded,
        finding=finding,
        usage=TurnUsage(
            input_tokens=u.input_tokens,
            output_tokens=u.output_tokens,
            cache_read_tokens=u.cache_read_tokens,
            cache_creation_tokens=u.cache_creation_tokens,
            cost=cost_uc.Money(currency=u.cost.currency, amount_micros=u.cost.amount_micros),
            cache_creation_known=u.cache_creation_known,
            cost_known=u.cost_known,
        ),
        context_truncated=o.context_truncated,
        paused=o.paused,
        notice=o.notice,
    )


@log
@account_scoped
@require_role("owner", "admin", "developer")
async def run_turn(
    demand_id: str, thread_id: str, body: RunTurn, idempotency_key: str = ""
) -> TurnOutcome:
    """Runs ONE turn, in the core.

    The idempotency key is MANDATORY in the core and the edge does not invent
    one: an agent turn spends money, and a key generated here would turn a
    network retry into double consumption with nobody asking.
    """
    o = await stubs.agent_stub().RunTurn(
        agent_pb2.RunTurnRequest(
            demand_id=demand_id,
            thread_id=thread_id,
            text=body.text,
            task_kind=body.task_kind,
            resource_id=body.resource_id,
            operator_note=body.operator_note,
            max_output_tokens=body.max_output_tokens,
            idempotency_key=idempotency_key,
        ),
        metadata=core.metadata(),
        timeout=_deadline(),
    )
    return _outcome(o)
