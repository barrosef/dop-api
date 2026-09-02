"""The attention box's routes — the use cases' HTTP/SSE translation, nothing more.

Zero decisions here: if a rule's `if` shows up in this layer, it belongs in
`app/usecases/attention.py`, or the gRPC port ends up without it.

**The SSE reuses `app/routers/stream.py`, and reimplements nothing.** That
module has already settled the five points that decide whether an SSE endpoint
is good or is a source of intermittent bugs — resuming, a client that goes away,
a slow consumer, an error after the first byte, the heartbeat — and each is
explained there. A second `async def` generating SSE frames in this file would be
a second place for each of those five things to age separately, and the day the
two disagreed nobody would know which is right. Only three things come out of
here: the event's name, the cursor and the call to the use case.

**Why a new event name (`attention`) and not the `event` from there.**
`stream.py`'s rule is a SMALL, stable set of names, so the cockpit does not need
one `addEventListener` per core type. `attention` gets in by not being the same
data: the body is an `AttentionUpdate` (a change in the queue), not a
`StreamEvent` (an event from the log). They are different renderers on the
screen — the same reason `log` is already separate from `event`. Reusing `event`
would force the cockpit to sniff the JSON's shape to know what to do with the
frame.

**Resuming, with an honest caveat.** `dop.v1.WatchAttentionRequest` has
`since_event_id`, so the `Last-Event-ID` header and the query parameter are
accepted and cross over, with the SAME precedence as the event stream (the
header wins — see `stream._cursor`). What does not close the circuit is the way
back: `dop.v1.AttentionUpdate` does not carry the id of the event that produced
it, so there is no HONEST `id:` to emit, and the EventSource has nothing to
resend on its own. Emitting the ITEM's id instead would be worse than emitting
nothing: it is not a position in the log, and it would go back to the core as a
meaningless cursor — exactly the mistake `stream.py` documents when it explains
why the `id:` is the CORE's event id and not a counter of ours. Until the core
publishes that id, the cursor serves whoever already knows their position in the
log (whoever also subscribes to `/stream/events`) and whoever kept it and returns
it through `?since_event_id=`.
"""

from fastapi import APIRouter, Header
from sse_starlette.sse import EventSourceResponse

from app.routers.stream import _cursor, _response
from app.usecases import attention as uc
from app.usecases.attention import (
    AttentionBox,
    AttentionGroup,
    AttentionItem,
    AttentionUpdate,
)

router = APIRouter(prefix="/api/v1", tags=["attention"])

__all__ = [
    "AttentionBox",
    "AttentionGroup",
    "AttentionItem",
    "AttentionUpdate",
    "router",
]

# The SSE event name of this queue — see the module's docstring.
ATTENTION = "attention"


@router.get("/attention", response_model=AttentionBox)
async def list_attention(
    include_resolved: bool = False, demand_id: str = "", page_size: int = 0
) -> AttentionBox:
    """The active account's box: the queue, the groups per demand and the badge.

    There is no "mark as read" route and no "dismiss" route, and the absence is
    the decision: the box is a projection, an item is born of and dies of an
    event, and an item that disappears with the problem unsolved is a comfortable
    lie. See the use case.
    """
    return await uc.list_attention(include_resolved, demand_id, page_size)


@router.get("/stream/attention")
async def stream_attention(
    since_event_id: str = "",
    last_event_id: str = Header(default="", alias="Last-Event-ID"),
) -> EventSourceResponse:
    """The box live — what opened and what closed.

    The use case is called HERE, outside the generator, on purpose: it is that
    call that fires `@account_scoped`. A refusal with no active account comes out
    as a real 400, with a JSON body, and not as a 200 that dies at the first
    frame.
    """
    source = uc.watch_attention(
        since_event_id=_cursor(last_event_id, since_event_id)
    )
    return _response(source, name=ATTENTION)
