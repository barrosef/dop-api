"""SSE routes — the core's gRPC streaming converted for the browser.

ADR-0013 (convention 1) commands: *server-side streaming for everything live;
the BFF converts it into SSE for the browser*. This module is that conversion.
Zero business decisions here — the rule is in `app/usecases/stream.py`.

Five points decide whether an SSE endpoint is good or is a source of
intermittent bugs. Each is settled below, with the why:

**Resuming.** The SSE protocol already has the mechanism (`Last-Event-ID`) and
the core already has the counterpart (`since_event_id`). Connecting the two is
just that: emitting `id:` with the CORE's event id — not a counter of ours,
which would mean nothing on the other side — and returning the `Last-Event-ID`
received as the cursor. The EventSource resends that header on its own at every
reconnection, so the browser that dropped and came back loses no event and gets
no duplicate: what guarantees that is the core's replay, and the only thing the
edge has to do is not break the chain.

**A client that goes away.** Handled in the use case (`_pump`): when the
EventSourceResponse detects `http.disconnect`, it cancels the task consuming the
generator, the generator's `finally` runs and the gRPC call is cancelled. The
subscription dies along with the tab.

**A slow consumer.** The core drops a slow subscriber with `UNAVAILABLE` and a
message asking it to reconnect with `since_event_id`. That becomes an SSE
`error` event with `retryable: true` and the last id emitted — rather than a
stream that dies in silence and leaves the cockpit showing old data as if it
were new.

**An error after the first byte.** Once the `200 OK` has been sent, there is no
swapping it for a 500: the status has already gone down the wire. That is why
the generator NEVER lets an exception escape once open — it becomes
`event: error`, with the detail passed through the SAME writer as the rest of
the edge (`_detail_for`), which does not let a 5xx message from the core leak.
An error BEFORE the first byte (no token, no active account) is still a normal
HTTP status: `@account_scoped` runs when the use case is called, inside the
handler, before the response starts.

**Heartbeat.** A periodic `: ping`, with the interval in `settings.sse_ping_s`
(the why of the number is there).
"""

import json
from typing import Annotated

from fastapi import APIRouter, Header, Query
from grpc import StatusCode
from grpc.aio import AioRpcError
from sse_starlette.event import ServerSentEvent
from sse_starlette.sse import EventSourceResponse

from app.platform.errors import _detail_for, http_status_for
from app.platform.logging.config import get_logger
from app.settings import settings
from app.usecases import stream as uc

router = APIRouter(prefix="/api/v1/stream", tags=["streaming"])

__all__ = ["router"]

# SSE event names: a SMALL, stable set, not the core's `type` vocabulary.
#
# The tempting thing would be to emit `event: dop.hierarchy.project.created`.
# But then the cockpit would need one addEventListener per existing event type,
# and would start breaking (silently: the event simply does not arrive) every
# time the core created a new type. With three fixed names, the client listens
# for three things and filters by `type` inside the JSON — which is data, not
# protocol.
EVENT = "event"
LOG = "log"
ERROR = "error"

# The codes worth retrying. The case that matters is the slow subscriber's
# UNAVAILABLE: the core drops it on purpose and expects a reconnection with a
# cursor.
_RETRYABLE = frozenset(
    {
        StatusCode.UNAVAILABLE,
        StatusCode.DEADLINE_EXCEEDED,
        StatusCode.RESOURCE_EXHAUSTED,
        StatusCode.ABORTED,
    }
)

# OUR text, for when the writer tells the core to be quiet.
#
# A real tension, and worth explaining: the slow subscriber's message
# ("reconnect with the since_event_id of the last event received") is precisely
# the one the cockpit would like to show — and it comes in an UNAVAILABLE, which
# is a 503, which `_detail_for` reduces to "internal error". Its rule is right
# and is not loosened for convenience: a 5xx detail from the core may carry a
# host, a query or a credential, and nobody wants to find that out from the
# user's screen. The way out is not to repeat the core's sentence, but to write
# our own — which carries no data from there. What the client needs in order to
# act is still in the structured fields (`code`, `retryable`, `since_event_id`),
# which are for machines and are not written prose.
_RECONNECT = "the connection was closed by the core; reconnect with since_event_id"


def _json(data: dict) -> str:
    # `default=str` covers the models' datetime with no serialization table of
    # ours. ensure_ascii is off because the payload carries free text in any
    # language.
    return json.dumps(data, ensure_ascii=False, default=str)


def _opening() -> ServerSentEvent:
    """The stream's first frame: only the `retry:`.

    An event with no `data:` is not dispatched by the EventSource — it only
    absorbs the `retry` field. It is the way to configure the client's
    reconnection interval without inventing a fake event the cockpit would have
    to learn to ignore.
    """
    return ServerSentEvent(retry=settings.sse_retry_ms)


def _error(exc: AioRpcError, last_id: str) -> ServerSentEvent:
    """A core error, in the middle of the stream, as an SSE event.

    The detail goes through the same `_detail_for` REST and gRPC already use — a
    5xx from the core leaks through no port, and there is no second writer to age
    separately. When it goes quiet and the error is retryable, `_RECONNECT` steps
    in, which is text of ours (see the constant's comment).

    `since_event_id` goes in the body even though it has already been emitted as
    `id:`: the client reconnecting with `EventSource` uses the automatic header,
    but whoever talks to this endpoint through `fetch` (or whoever reloaded the
    page) needs the cursor somewhere they can read.
    """
    status = http_status_for(exc.code())
    retryable = exc.code() in _RETRYABLE
    detail = _detail_for(exc, status)
    if retryable and status >= 500:
        detail = _RECONNECT
    return ServerSentEvent(
        event=ERROR,
        data=_json(
            {
                "status": status,
                "code": exc.code().name,
                "detail": detail,
                # False does NOT mean "stop trying" for the EventSource, which
                # reconnects on its own anyway — it means the client should close
                # the connection instead of insisting. That is why the cockpit
                # needs this field and not only the end of the stream.
                "retryable": retryable,
                "since_event_id": last_id,
            }
        ),
    )


async def _sse_events(source, *, name: str):
    """Translates the use case's generator into SSE frames, letting no error escape.

    After the `yield _opening()` the 200 status has already gone down the wire.
    From then on, no exception may propagate: propagating would become a stream
    cut in the middle, which on the browser's side is indistinguishable from a
    bad network. It becomes `event: error`.
    """
    last_id = ""
    yield _opening()
    try:
        async for item in source:
            data = item.model_dump()
            # `id:` only when there is one. Emitting an empty one would make the
            # browser send an empty Last-Event-ID on reconnection —
            # indistinguishable from "I have never seen anything" — and the
            # demand stream, which has no cursor, would start pretending it has
            # one.
            event_id = data.get("id") or None
            if event_id:
                last_id = event_id
            yield ServerSentEvent(event=name, id=event_id, data=_json(data))
    except AioRpcError as exc:
        get_logger().warning(
            "stream interrupted by the core", code=str(exc.code()), stream=name
        )
        yield _error(exc, last_id)
    except Exception as exc:  # noqa: BLE001 - see the comment
        # A failure of ours. The same treatment as the ErrorInterceptor: whole in
        # the log, generic on the wire. Letting it propagate would cut the
        # response in half.
        get_logger().error("unhandled error in the stream", error=str(exc), stream=name)
        yield ServerSentEvent(
            event=ERROR,
            data=_json(
                {
                    "status": 500,
                    "code": "INTERNAL",
                    "detail": "internal error",
                    "retryable": False,
                    "since_event_id": last_id,
                }
            ),
        )


def _response(source, *, name: str) -> EventSourceResponse:
    return EventSourceResponse(
        _sse_events(source, name=name), ping=settings.sse_ping_s
    )


def _cursor(last_event_id: str, since_event_id: str) -> str:
    """The header beats the query parameter, and the order is not arbitrary.

    `Last-Event-ID` is RESENT by the browser on every automatic reconnection,
    with the most recent id it processed. The URL, that one is frozen at the
    moment the EventSource was created — its `?since_event_id=` ages on the first
    reconnection. Preferring the query would bring already seen events back on
    every network drop.

    The parameter still exists because the EventSource does not let the client
    set a header: on the FIRST connection after an F5, the cursor the cockpit
    kept has only this path to reach here.
    """
    return last_event_id or since_event_id


@router.get("/events")
async def stream_account_events(
    aggregate: Annotated[list[str] | None, Query()] = None,
    types: Annotated[list[str] | None, Query()] = None,
    since_event_id: str = "",
    last_event_id: str = Header(default="", alias="Last-Event-ID"),
) -> EventSourceResponse:
    """The active account's events — the cockpit's timeline and attention box.

    The use case is called HERE, outside the generator, on purpose: it is that
    call that fires `@account_scoped`. A refusal with no active account comes out
    as a real 400, with a JSON body, and not as a 200 that dies at the first
    frame.
    """
    source = uc.watch_account_events(
        since_event_id=_cursor(last_event_id, since_event_id),
        aggregate=aggregate or [],
        types=types or [],
    )
    return _response(source, name=EVENT)


@router.get("/demands/{demand_id}")
async def stream_demand(demand_id: str) -> EventSourceResponse:
    """One demand's events — what makes its chat and its timeline live.

    No cursor: `dop.v1.WatchDemandRequest` has no `since_event_id`, so this
    stream does not accept `Last-Event-ID` — and, consistently, emits no `id:`.
    Pretending to resume here would give the cockpit the impression that nothing
    was lost in a reconnection. Whoever reconnects rereads the demand's dossier.
    """
    return _response(uc.watch_demand(demand_id), name=EVENT)


@router.get("/sandboxes/{sandbox_id}/logs")
async def stream_sandbox_logs(
    sandbox_id: str,
    source: str = "",
    service: str = "",
    test_type: str = "",
) -> EventSourceResponse:
    """A sandbox's log tail. Also with no cursor, for the same reason."""
    source = uc.tail_sandbox_logs(
        sandbox_id, source=source, service=service, test_type=test_type
    )
    return _response(source, name=LOG)
