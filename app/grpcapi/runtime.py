"""The turn-running gRPC servicer — a protobuf adapter, nothing more.

The runtime does NOT live in the BFF (ADR-0016): this servicer calls the same
function from `app/usecases/runtime.py` the REST route calls, and that one calls
the core.

An unavailable provider, a refused credential and an account with no agent
integration arrive from the core with the right status and cross through the
usual error interceptor — there is no special handling here, and there must not
be: it would be a second translation of the same error, diverging from REST's at
the first adjustment.
"""

from app.grpcapi.gen.dop.bff.v1 import runtime_pb2 as bff
from app.grpcapi.gen.dop.bff.v1 import runtime_pb2_grpc as bff_grpc
from app.usecases import runtime as uc


def _outcome(o: uc.TurnOutcome) -> bff.TurnOutcome:
    msg = bff.TurnOutcome(
        demand_id=o.demand_id,
        thread_id=o.thread_id,
        provider=o.provider,
        routing=bff.RoutingView(
            task_kind=o.routing.task_kind,
            model=o.routing.model,
            effort=o.routing.effort,
            effort_applied=o.routing.effort_applied,
            # The justification goes WHOLE, with the provenance up front: it is
            # the only auditable part of the routing decision (ADR-0008 §3).
            reason=o.routing.reason,
            from_agent_card=o.routing.from_agent_card,
        ),
        reply=o.reply,
        message_ids=o.message_ids,
        concluded=o.concluded,
        usage=bff.TurnUsage(
            input_tokens=o.usage.input_tokens,
            output_tokens=o.usage.output_tokens,
            cache_read_tokens=o.usage.cache_read_tokens,
            cache_creation_tokens=o.usage.cache_creation_tokens,
            cache_creation_known=o.usage.cache_creation_known,
            cost_known=o.usage.cost_known,
        ),
        context_truncated=o.context_truncated,
        paused=o.paused,
        notice=o.notice,
    )
    # Absent ≠ zeroed: a finding only exists once the thread has concluded, and
    # an empty FindingRef would say it concluded without publishing anything.
    if o.finding is not None:
        msg.finding.CopyFrom(bff.FindingRef(id=o.finding.id, title=o.finding.title))
    return msg


class RuntimeServicer(bff_grpc.RuntimeServiceServicer):
    async def RunTurn(self, request: bff.RunTurnRequest, context) -> bff.TurnOutcome:
        body = uc.RunTurn(
            text=request.text,
            task_kind=request.task_kind or "implementation",
            resource_id=request.resource_id,
            operator_note=request.operator_note,
            max_output_tokens=request.max_output_tokens or 8192,
        )
        return _outcome(
            await uc.run_turn(
                request.demand_id, request.thread_id, body, request.idempotency_key
            )
        )
