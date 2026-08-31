"""Servicer gRPC de execução de turno — adaptador protobuf, nada mais.

O runtime NÃO vive no BFF (ADR-0023): este servicer chama a mesma função de
`app/usecases/runtime.py` que a rota REST chama, e ela chama o núcleo.

Provedor indisponível, credencial recusada e conta sem integração de agente
chegam do núcleo com o status certo e atravessam pelo interceptor de erro de
sempre — não há tratamento especial aqui, e não deve haver: seria uma segunda
tradução do mesmo erro, divergindo da do REST no primeiro ajuste.
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
            # A justificativa vai INTEIRA, com a proveniência na frente: é a
            # única parte auditável da decisão de roteamento (ADR-0011 §3).
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
    # Ausente ≠ zerado: achado só existe quando a thread concluiu, e um
    # FindingRef vazio diria que concluiu sem publicar nada.
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
