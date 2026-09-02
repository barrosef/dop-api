"""The streaming gRPC servicer — a protobuf adapter over the use cases.

Symmetric to `app/routers/stream.py`: it receives a message, iterates the SAME
function from `app/usecases/stream.py`, emits a message. No decisions here —
neither authorization nor a call to the core.

Why the gRPC port offers this too, instead of telling everybody to use SSE:
`dop-cli` and the sandboxes' agents already speak gRPC and already carry the
token in the metadata. Forcing them to implement `text/event-stream` (with
reconnection, `Last-Event-ID` and text parsing) to follow exactly the same
events would be asking for a second client for the same data.

Two differences from SSE, and both are the TRANSPORT's, not the rule's:

* **An error in the middle of the stream.** Here it propagates as an exception
  and the `ErrorInterceptor` turns it into a status — gRPC carries the status in
  the trailers, so failing after the first item is possible. In SSE it is not
  (the 200 has already gone), and that is why there the error becomes an `error`
  event.
* **Heartbeat.** There is no `: ping` here: HTTP/2 has its own keepalive, and the
  BFF's channel already configures it (`grpc.keepalive_time_ms`). Inventing a
  ping event would pollute the stream with an item that is not an event.

Every method is a GENERATOR function. It is not style: `grpc.aio` chooses how to
run the handler from that — see `interceptors.py`'s docstring.
"""

from app.grpcapi.gen.dop.bff.v1 import stream_pb2 as bff
from app.grpcapi.gen.dop.bff.v1 import stream_pb2_grpc as bff_grpc
from app.usecases import stream as uc


def _event(e: uc.StreamEvent) -> bff.StreamEvent:
    msg = bff.StreamEvent(
        id=e.id, type=e.type, aggregate=e.aggregate, aggregate_id=e.aggregate_id
    )
    # It only fills in what exists: an absent message field and a zeroed one are
    # different things, and an empty payload assigned would invent content where
    # there is none.
    if e.payload:
        msg.payload.update(e.payload)
    if e.occurred_at is not None:
        msg.occurred_at.FromDatetime(e.occurred_at)
    return msg


def _log_line(ll: uc.LogLine) -> bff.LogLine:
    msg = bff.LogLine(source=ll.source, service=ll.service, line=ll.line)
    if ll.at is not None:
        msg.at.FromDatetime(ll.at)
    return msg


class StreamServicer(bff_grpc.StreamServiceServicer):
    async def WatchEvents(self, request: bff.WatchEventsRequest, context):
        # The use case is called BEFORE the first `yield`, and it is that call
        # that fires @account_scoped — a refusal comes out as a status, without
        # the client having received an empty stream that looks like success.
        source = uc.watch_account_events(
            since_event_id=request.since_event_id,
            aggregate=list(request.aggregate),
            types=list(request.types),
        )
        async for event in source:
            yield _event(event)

    async def WatchDemand(self, request: bff.WatchDemandRequest, context):
        async for event in uc.watch_demand(request.demand_id):
            yield _event(event)

    async def TailLogs(self, request: bff.TailLogsRequest, context):
        source = uc.tail_sandbox_logs(
            request.sandbox_id,
            source=request.source,
            service=request.service,
            test_type=request.test_type,
        )
        async for line in source:
            yield _log_line(line)
