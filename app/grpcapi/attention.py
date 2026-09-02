"""The attention box's gRPC servicer — a protobuf adapter over the use cases.

Symmetric to `app/routers/attention.py`: it receives a message, calls the SAME
function from `app/usecases/attention.py`, returns a message. No decisions here
— neither authorization, nor a call to the core, nor the grouping by demand
(that comes ready from the use case, or the two ports would group in different
ways and the same queue would appear in two orders).

Why the gRPC port offers the box too, instead of telling everybody to use
REST+SSE: `dop-cli` already speaks gRPC and already carries the token in the
metadata, and "where am I needed now" is exactly the question one asks from the
terminal. Forcing it to implement `text/event-stream` to follow the same queue
would be asking for a second client for the same data.

`WatchAttention` is a GENERATOR function. It is not style: `grpc.aio` chooses how
to run the handler from that — see `interceptors.py`'s docstring.
"""

from app.grpcapi.gen.dop.bff.v1 import attention_pb2 as bff
from app.grpcapi.gen.dop.bff.v1 import attention_pb2_grpc as bff_grpc
from app.usecases import attention as uc

# Name ↔ enum in the outbound direction. The use case's table goes from the
# CORE's enum to the name; this one goes from the name to the EDGE's enum. They
# are two different contracts, and a single dictionary (inverted on the fly)
# would hide that.
ENUM_BY_KIND: dict[str, int] = {
    "thread_blocked": bff.ATTENTION_KIND_THREAD_BLOCKED,
    "gate_pending": bff.ATTENTION_KIND_GATE_PENDING,
    "pr_review": bff.ATTENTION_KIND_PR_REVIEW,
    "merge_conflict": bff.ATTENTION_KIND_MERGE_CONFLICT,
    "directive": bff.ATTENTION_KIND_DIRECTIVE,
    "budget_exceeded": bff.ATTENTION_KIND_BUDGET_EXCEEDED,
    "integration_broken": bff.ATTENTION_KIND_INTEGRATION_BROKEN,
}

ENUM_BY_CHANGE: dict[str, int] = {
    "opened": bff.ATTENTION_CHANGE_OPENED,
    "resolved": bff.ATTENTION_CHANGE_RESOLVED,
}


def _item(i: uc.AttentionItem) -> bff.AttentionItem:
    msg = bff.AttentionItem(
        id=i.id,
        kind=ENUM_BY_KIND.get(i.kind, bff.ATTENTION_KIND_UNSPECIFIED),
        target_kind=i.target_kind,
        target_id=i.target_id,
        demand_id=i.demand_id,
        title=i.title,
        summary=i.summary,
        priority=i.priority,
    )
    # It only fills in what exists: a zeroed Timestamp would say "opened in
    # 1970", and on the other side HasField would answer that there is a date.
    if i.opened_at is not None:
        msg.opened_at.FromDatetime(i.opened_at)
    if i.resolved_at is not None:
        msg.resolved_at.FromDatetime(i.resolved_at)
    return msg


class AttentionServicer(bff_grpc.AttentionServiceServicer):
    async def ListAttention(
        self, request: bff.ListAttentionRequest, context
    ) -> bff.AttentionBox:
        box = await uc.list_attention(
            request.include_resolved, request.demand_id, request.page_size
        )
        return bff.AttentionBox(
            items=[_item(i) for i in box.items],
            groups=[
                bff.AttentionGroup(
                    demand_id=g.demand_id, items=[_item(i) for i in g.items]
                )
                for g in box.groups
            ],
            open_total=box.open_total,
        )

    async def WatchAttention(self, request: bff.WatchAttentionRequest, context):
        # The use case is called BEFORE the first `yield`, and it is that call
        # that fires @account_scoped — a refusal comes out as a status, without
        # the client having received an empty stream that looks like success.
        source = uc.watch_attention(since_event_id=request.since_event_id)
        async for update in source:
            yield bff.AttentionUpdate(
                change=ENUM_BY_CHANGE.get(
                    update.change, bff.ATTENTION_CHANGE_UNSPECIFIED
                ),
                item=_item(update.item),
            )
