"""The attention box's use cases — the single queue of "where am I needed".

The same discipline as `identity` and `hierarchy`: the rule lives here and only
here; `app/routers/attention.py` translates HTTP/SSE and
`app/grpcapi/attention.py` translates protobuf, both calling these SAME
functions. See `app/usecases/identity.py`'s docstring for why the decorators
live in the use case — authorization pinned to the router would leave the gRPC
door open.

The box is the queue across ALL the active account's demands, answering "where
am I needed, and in what order" (the conversation-and-attention spec §3). It is
not the chat: it is what leads to the right chat. Five demands with three
threads each are fifteen conversations, and without this queue the multi-agent
model drowns the dev — the two rise together or neither rises (risk R-2).

**Three things this module does NOT do, and each is a decision:**

1. **It does not recompute priority.** It is DERIVED by the core — the kind's
   impact, then age, with age breaking ties only WITHIN the band
   (`internal/domain/attention/entity.go`). A second ruler here would make the
   queue stop having a single order, which is exactly what it exists to offer: a
   three-day-old exploratory question would jump ahead of a three-minute-old
   production conflict. `list_attention` preserves the order the core sent,
   between the groups included.

2. **It does not count the badge.** `open_total` comes from the core and counts
   the WHOLE account, independent of the page and of the demand filter. Counting
   the page's items would give a badge that changes when the dev paginates — and
   a badge that goes down on its own is a badge nobody trusts.

3. **It does not close an item.** There is no "mark as read" nor "dismiss",
   neither here nor in the edge's contract. The box is a PROJECTION (ADR-0004):
   an item is born of an event and dies of an event, and what closes it is the
   FACT — the gate decided, the thread unblocked. An item that disappears with
   the problem unsolved is a comfortable lie, and a box that lies becomes a box
   that is ignored (risk R-1).

**What it does that the core does not:** it groups by demand. The spec asks for
a box groupable by demand, and fifteen pending items in a flat list do not say
"demand 3 is waiting for you in three places". Is grouping reordering? No: each
group's items come out in the order they arrived, and the groups come out in the
order of each demand's FIRST occurrence in the queue — that is, in the order of
each one's most urgent item. No new number is invented on the way.

The stream follows `app/usecases/stream.py`'s mechanics, imported and not
copied: no deadline, cancellation in the `finally`, `@account_scoped` holding at
the opening. The why of each of those three is in that module's docstring —
repeating them here would create a second place for them to age separately.
"""

from collections.abc import AsyncIterator
from datetime import UTC, datetime

from pydantic import BaseModel, Field

from app.coreclient import stubs
from app.coreclient.client import core
from app.coreclient.gen.dop.v1 import attention_pb2, common_pb2
from app.platform.logging.decorator import log
from app.platform.security.decorator import account_scoped
from app.settings import settings
from app.usecases.stream import _pump

# Name ↔ enum in one place only, as in `resource` and `knowledge`. The
# vocabulary is the core DOMAIN's (attention.Kind, in Go), and not a translation
# of ours: that way what appears on the screen is the same word that appears in
# the core's log when somebody goes to investigate why an item entered the
# queue.
_KIND_BY_ENUM: dict[int, str] = {
    attention_pb2.AttentionItem.KIND_THREAD_BLOCKED: "thread_blocked",
    attention_pb2.AttentionItem.KIND_GATE_PENDING: "gate_pending",
    attention_pb2.AttentionItem.KIND_PR_REVIEW: "pr_review",
    attention_pb2.AttentionItem.KIND_MERGE_CONFLICT: "merge_conflict",
    attention_pb2.AttentionItem.KIND_DIRECTIVE: "directive",
    attention_pb2.AttentionItem.KIND_BUDGET_EXCEEDED: "budget_exceeded",
    attention_pb2.AttentionItem.KIND_INTEGRATION_BROKEN: "integration_broken",
}

_CHANGE_BY_ENUM: dict[int, str] = {
    attention_pb2.AttentionUpdate.CHANGE_OPENED: "opened",
    attention_pb2.AttentionUpdate.CHANGE_RESOLVED: "resolved",
}


def kind_name(value: int) -> str:
    return _KIND_BY_ENUM.get(value, "")


def _deadline() -> float:
    return settings.core_deadline_s


# ── the edge's models ───────────────────────────────────────────────────────


class AttentionItem(BaseModel):
    """A pending item that requires a HUMAN DECISION.

    What does not require a decision does not go in: status and progress stay in
    the cockpit. A noisy box becomes noise and is ignored (the spec's risk R-1).
    """

    id: str = ""
    kind: str = ""
    # Where clicking leads. The cockpit resolves the route from the pair;
    # keeping the finished route would tie the edge to the screen's design.
    target_kind: str = ""
    target_id: str = ""
    # Empty on an ACCOUNT item (a broken integration) — which belongs to no
    # demand. It is by this field that the box groups.
    demand_id: str = ""
    title: str = ""
    summary: str = ""
    # DERIVED by the core. A lower number = more urgent. It is not recomputed here.
    priority: int = 0
    # None = no date, and not epoch zero: 1970 in a queue ordered by age would
    # show up as the oldest item in the world.
    opened_at: datetime | None = None
    resolved_at: datetime | None = None


class AttentionGroup(BaseModel):
    """One demand's items, in the order the core gave them."""

    # Empty = the ACCOUNT items. A group like the others, and not a leftover at
    # the end: a broken integration affects the whole account and usually leads
    # the queue.
    demand_id: str = ""
    items: list[AttentionItem] = Field(default_factory=list)


class AttentionBox(BaseModel):
    items: list[AttentionItem] = Field(default_factory=list)
    groups: list[AttentionGroup] = Field(default_factory=list)
    # The BADGE, from the core: the whole account's OPEN items, independent of
    # this page and of the demand filter.
    open_total: int = 0


class AttentionUpdate(BaseModel):
    """A change in the box — not the whole box.

    `change` is the notice's TRUTH. On `resolved`, the core identifies what
    closed by the TARGET (`kind` + `target_kind` + `target_id`), because whoever
    closes it knows the target and not the projection's id: the item arrives
    with no `id`, no title and no dates. The edge passes it on like that —
    inventing an id here, or stamping `resolved_at` with the BFF's clock, would
    give the cockpit a datum nobody measured.
    """

    change: str = ""
    # The id of the EVENT that produced the notice — the POSITION in the log,
    # not the item's id.
    #
    # It is the resume cursor: the SSE emitter uses this field as `id:`, and the
    # client returns it in `since_event_id` when reconnecting. The ITEM's id
    # would not do: it is not a position in the log, and sending it back would
    # ask the core for something that does not exist.
    #
    # The core started sending it after this edge was written; while it did not,
    # the stream emitted no `id:` at all, which was right — an invented cursor is
    # worse than an absent one.
    id: str = ""
    item: AttentionItem


# ── translating the core into the edge ──────────────────────────────────────


def _when(msg, field: str) -> datetime | None:
    """An absent timestamp becomes None, not epoch zero.

    The same rule as `usecases/stream.py`: HasField, because a timestamp is a
    MESSAGE field and absent ≠ zeroed.
    """
    if not msg.HasField(field):
        return None
    return getattr(msg, field).ToDatetime(tzinfo=UTC)


def _item(it: attention_pb2.AttentionItem) -> AttentionItem:
    return AttentionItem(
        id=it.id,
        kind=kind_name(it.kind),
        target_kind=it.target_kind,
        target_id=it.target_id,
        # `demand` is an optional message: an account item has no demand, and
        # reading `it.demand.id` from an absent field would return "" — which is
        # what we want, but by accident. HasField says the same thing on
        # purpose.
        demand_id=it.demand.id if it.HasField("demand") else "",
        title=it.title,
        summary=it.summary,
        priority=it.priority,
        opened_at=_when(it, "opened_at"),
        resolved_at=_when(it, "resolved_at"),
    )


def _group_by_demand(items: list[AttentionItem]) -> list[AttentionGroup]:
    """Groups by demand PRESERVING the core's order.

    Two orders come out of here, and neither of them is new:

      - within the group, the order in which the items arrived;
      - between groups, the order of each demand's FIRST appearance in the queue
        — that is, that of each one's most urgent item.

    That is why the grouping is not a second ruler of priority: it compares
    nothing. A `sorted` by the group's urgency would give the same result today
    and would start diverging the day the core's ruler changed — which is exactly
    the divergence this module exists not to have.

    A plain `dict` because insertion has been ordered since Python 3.7, and
    relying on that here is more readable than carrying an OrderedDict to say
    the same thing.
    """
    groups: dict[str, AttentionGroup] = {}
    for item in items:
        group = groups.get(item.demand_id)
        if group is None:
            group = AttentionGroup(demand_id=item.demand_id)
            groups[item.demand_id] = group
        group.items.append(item)
    return list(groups.values())


# ── use cases ───────────────────────────────────────────────────────────────


@log
@account_scoped
async def list_attention(
    include_resolved: bool = False, demand_id: str = "", page_size: int = 0
) -> AttentionBox:
    """The active account's box: the queue, the groups per demand and the badge.

    No `page_token`: `dop.v1.AttentionService` returns no page cursor, and
    announcing one at the edge would be promising a navigation the source cannot
    deliver. Whoever wants to know whether they are seeing the whole box compares
    `open_total` with the size of `items`.
    """
    request = attention_pb2.ListAttentionRequest(
        include_resolved=include_resolved,
        page=common_pb2.PageRequest(size=page_size),
    )
    # It only fills it in when there is a filter: an empty DemandRef would be a
    # demand with id "", and the core would read it as a filter, not as the
    # absence of one.
    if demand_id:
        request.demand.CopyFrom(common_pb2.DemandRef(id=demand_id))
    resp = await stubs.attention_stub().ListAttention(
        request, metadata=core.metadata(), timeout=_deadline()
    )
    items = [_item(i) for i in resp.items]
    return AttentionBox(items=items, groups=_group_by_demand(items), open_total=resp.open_total)


@account_scoped
def watch_attention(since_event_id: str = "") -> AsyncIterator[AttentionUpdate]:
    """The box live: what opened and what closed, item by item.

    A plain function (not an `async def`) returning the generator, and the
    mechanics coming from `usecases.stream._pump`: it is the same way of doing a
    stream the rest of the edge uses, with `@account_scoped` refusing at the
    moment of OPENING and the gRPC call's cancellation guaranteed in the
    `finally`. See `app/usecases/stream.py`'s docstring for the why of each of
    those three decisions — this module has no second way of doing the same
    thing.

    `since_event_id` crosses INTACT into the core, which drains the log from
    there and splices into the live stream. The edge keeps nobody's read
    position — if it did, it would have state, and the BFF has no state
    (ADR-0012).
    """
    call = stubs.attention_stub().WatchAttention(
        attention_pb2.WatchAttentionRequest(since_event_id=since_event_id),
        metadata=core.metadata(),
    )
    return _pump(call, _update, label="attention", since_event_id=since_event_id)


def _update(u: attention_pb2.AttentionUpdate) -> AttentionUpdate:
    # `item` is a message field: an update with no item does not become a zeroed
    # item. It should not happen, and if it does the client sees an empty item
    # instead of getting a KeyError from inside the stream.
    item = _item(u.item) if u.HasField("item") else AttentionItem()
    return AttentionUpdate(change=_CHANGE_BY_ENUM.get(u.change, ""), item=item, id=u.event_id)
