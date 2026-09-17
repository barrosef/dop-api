"""Streaming use cases — what is happening NOW, on both ports.

The same discipline as `identity` and `hierarchy`: the rule lives here, adapters
translate. See `app/usecases/identity.py`'s docstring for why the decorators
live in the use case and not in the router.

What this module converts: the core's three *server-streaming* RPCs
(`EventService.WatchEvents`, `DemandService.WatchDemand`,
`ExecutionService.StreamLogs`) into asynchronous generators of the edge's
models. `app/routers/stream.py` wraps them in SSE; `app/grpcapi/stream.py`
re-emits them as a gRPC stream. Neither of the two decides anything.

Three structural decisions, because a stream has traps a unary call does not:

1. **No deadline.** Every unary use case sends `timeout=core_deadline_s`. Not
   here: a healthy subscription lasts hours, and a deadline would kill it
   halfway by definition. What ends the stream is the client going away, the
   core closing, or an error.

2. **Cancellation is our obligation.** If the consumer stops iterating (the
   browser closed the tab, the CLI hit Ctrl-C), the generator is finalized and
   the `finally` CANCELS the gRPC call. Without it the subscription stays alive
   on the other side of the network — the core's `ctx.Done()` never fires and
   its watcher stays in the fan-out forever. It is a goroutine leak caused by
   carelessness here.

3. **@account_scoped holds at the OPENING.** The decorator is not asynchronous
   for a generator function, so the check runs when the generator is CREATED —
   before the first item. It is exactly what we want: a stream must not be the
   door that is born open, and refusing only at the first iteration would
   already be late (the SSE would have answered 200 already).

`@log` is deliberately NOT used: it measures a *call*'s duration, and a call
that returns a generator lasts microseconds. What matters in a stream is the
CONNECTION's duration and how many items went out — recorded by hand in the
`finally`.
"""

import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime

from google.protobuf import json_format
from pydantic import BaseModel, Field

from app.coreclient import stubs
from app.coreclient.client import core
from app.coreclient.gen.dop.v1 import demand_pb2, event_pb2, execution_pb2
from app.platform.logging.config import FIELD_DURATION_MS, get_logger
from app.platform.security.decorator import account_scoped

# ── the edge's models ───────────────────────────────────────────────────────


class StreamEvent(BaseModel):
    """An event from the log, already in the shape both ports emit.

    `payload` is a dict (not a Struct) on purpose: it is the common format
    between SSE's JSON and gRPC's protobuf, and it is what guarantees the two
    adapters render the SAME thing instead of each translating the Struct its own
    way.
    """

    id: str = ""
    type: str = ""
    aggregate: str = ""
    aggregate_id: str = ""
    payload: dict = Field(default_factory=dict)
    occurred_at: datetime | None = None


class LogLine(BaseModel):
    source: str = ""
    service: str = ""
    line: str = ""
    at: datetime | None = None


# ── translating the core into the edge ──────────────────────────────────────


def _when(msg, field: str) -> datetime | None:
    """An absent timestamp becomes None, not epoch zero.

    1970-01-01 in a timeline is worse than nothing: it shows up as an ancient
    event at the top of the screen instead of showing up as what it is — without
    a time.
    """
    if not msg.HasField(field):
        return None
    return getattr(msg, field).ToDatetime(tzinfo=UTC)


def _payload(msg, field: str = "payload") -> dict:
    # Struct → dict via json_format: it is protobuf's canonical conversion, and
    # it preserves numbers, booleans and nesting with no table of ours in
    # between.
    return json_format.MessageToDict(getattr(msg, field)) if msg.HasField(field) else {}


def _event(env: event_pb2.EventEnvelope) -> StreamEvent:
    return StreamEvent(
        id=env.id,
        type=env.type,
        aggregate=env.aggregate,
        aggregate_id=env.aggregate_id,
        payload=_payload(env),
        occurred_at=_when(env, "occurred_at"),
    )


def _demand_event(ev: demand_pb2.DemandEvent, demand_id: str) -> StreamEvent:
    """The core's DemandEvent has neither an id nor an aggregate_id.

    The `id` stays EMPTY rather than invented: it is what the SSE emits as `id:`
    and what the client would return as a resume cursor. An id fabricated here
    would promise a resume `WatchDemand` cannot deliver — the core's contract has
    no `since_event_id` for that RPC. The `aggregate_id` is the demand itself,
    which the caller already supplied.
    """
    return StreamEvent(
        id="",
        type=ev.type,
        aggregate=ev.aggregate or "demand",
        aggregate_id=demand_id,
        payload=_payload(ev),
        occurred_at=_when(ev, "at"),
    )


def _log_line(ll: execution_pb2.LogLine) -> LogLine:
    return LogLine(source=ll.source, service=ll.service, line=ll.line, at=_when(ll, "at"))


# ── the three streams' common mechanics ─────────────────────────────────────


async def _pump(call, translate, *, label: str, **log_fields) -> AsyncIterator:
    """Iterates the core's streaming call and guarantees the cancellation.

    The `finally` is this module's heart. It runs both on a natural end and when
    the consumer abandons the generator (a closed tab, Ctrl-C, an error in the
    adapter): in both cases the gRPC call is cancelled and the core sees the
    context end. Without it, a client going away becomes an orphaned
    subscription over there.
    """
    started = time.perf_counter()
    emitted = 0
    get_logger().info("stream opened", stream=label, **log_fields)
    try:
        async for msg in call:
            emitted += 1
            yield translate(msg)
    finally:
        # `cancel()` is idempotent and returns False if the call has already
        # finished — always calling it is cheaper than finding out whether we
        # need to.
        call.cancel()
        get_logger().info(
            "stream closed",
            stream=label,
            emitted=emitted,
            **log_fields,
            **{FIELD_DURATION_MS: round((time.perf_counter() - started) * 1000)},
        )


# ── use cases ───────────────────────────────────────────────────────────────


@account_scoped
def watch_account_events(
    *,
    since_event_id: str = "",
    aggregate: list[str] | None = None,
    types: list[str] | None = None,
) -> AsyncIterator[StreamEvent]:
    """The active account's events — what feeds the timeline and the attention box.

    `since_event_id` is the resume cursor and crosses INTACT into the core: it is
    the core that drains the log from there and splices into the live stream with
    no gap and no duplicate. The edge keeps nobody's read position — if it did,
    it would have state, and the BFF has no state (ADR-0012).

    A plain function (not an `async def`) returning the generator: that way
    `@account_scoped` refuses at the moment of opening, and not at the first
    iteration.
    """
    call = stubs.event_stub().WatchEvents(
        event_pb2.WatchEventsRequest(
            aggregate=aggregate or [],
            types=types or [],
            since_event_id=since_event_id,
        ),
        metadata=core.metadata(),
    )
    return _pump(call, _event, label="account_events", since_event_id=since_event_id)


@account_scoped
def watch_demand(demand_id: str) -> AsyncIterator[StreamEvent]:
    """One demand's events — a thread message, a stage, a published finding."""
    call = stubs.demand_stub().WatchDemand(
        demand_pb2.WatchDemandRequest(demand_id=demand_id),
        metadata=core.metadata(),
    )
    return _pump(
        call,
        lambda ev: _demand_event(ev, demand_id),
        label="demand",
        demand_id=demand_id,
    )


@account_scoped
def tail_sandbox_logs(
    sandbox_id: str, *, source: str = "", service: str = "", test_type: str = ""
) -> AsyncIterator[LogLine]:
    """A sandbox's log tail. The filters pass on without interpretation."""
    call = stubs.execution_stub().StreamLogs(
        execution_pb2.StreamLogsRequest(
            sandbox_id=sandbox_id,
            source=source,
            service=service,
            test_type=test_type,
        ),
        metadata=core.metadata(),
    )
    return _pump(call, _log_line, label="sandbox_logs", sandbox_id=sandbox_id)
