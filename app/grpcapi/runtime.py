"""Servicer gRPC do AgentRuntime — adaptador protobuf sobre o caso de uso.

Simétrico a `app/routers/runtime.py`: recebe mensagem, chama a MESMA função de
`app/usecases/runtime.py`, devolve mensagem. Nenhuma decisão aqui — nem qual
modelo atende qual trabalho, nem o que é estouro, nem o que é conclusão.

Dois cuidados que este arquivo não pode perder:

  - **indisponibilidade de terceiro tem status próprio.** Provedor de agente
    fora do ar vira `UNAVAILABLE` com o motivo legível no detalhe, e nunca
    `INTERNAL`: `INTERNAL` diz "o BFF quebrou", que manda o cliente (o dop-cli,
    o sandbox) tratar como bug nosso em vez de reconfigurar a credencial ou
    tentar mais tarde. O detalhe CRU do SDK do fornecedor não entra no status —
    ele carrega URL, cabeçalho e às vezes prefixo de chave;
  - **`ausente ≠ zerado` atravessa.** `cache_creation_known` e `cost_known`
    viajam para que o zero ao lado deles não seja lido como afirmação. Achado
    ausente fica ausente: `finding` só é preenchido quando houve conclusão.
"""

import grpc

from app.grpcapi.gen.dop.bff.v1 import cost_pb2 as bff_cost
from app.grpcapi.gen.dop.bff.v1 import runtime_pb2 as bff
from app.grpcapi.gen.dop.bff.v1 import runtime_pb2_grpc as bff_grpc
from app.platform.logging.config import get_logger
from app.runtime.errors import AgentProviderUnavailable
from app.usecases import cost as cost_uc
from app.usecases import runtime as uc


def _money(m: cost_uc.Money) -> bff_cost.Money:
    return bff_cost.Money(currency=m.currency, amount_micros=m.amount_micros)


def _budget(b: cost_uc.BudgetView) -> bff_cost.BudgetView:
    msg = bff_cost.BudgetView(
        scope=b.scope,
        scope_id=b.scope_id,
        limit=_money(b.limit),
        spent=_money(b.spent),
    )
    # Escopo sem teto não tem sobra a mostrar: um Money zerado diria "acabou o
    # dinheiro", que é o oposto de "sem teto".
    if b.remaining is not None:
        msg.remaining.CopyFrom(_money(b.remaining))
    return msg


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
            cost=_money(o.usage.cost),
            cache_creation_known=o.usage.cache_creation_known,
            cost_known=o.usage.cost_known,
        ),
        context_truncated=o.context_truncated,
        paused=o.paused,
        notice=o.notice,
        budgets=[_budget(b) for b in o.budgets],
        warnings=o.warnings,
    )
    # Achado ausente fica AUSENTE: um FindingRef vazio pareceria um achado sem
    # título, e a thread teria "concluído" com um registro de nada.
    if o.finding is not None:
        msg.finding.CopyFrom(bff.FindingRef(id=o.finding.id, title=o.finding.title))
    return msg


class RuntimeServicer(bff_grpc.RuntimeServiceServicer):
    async def RunTurn(self, request: bff.RunTurnRequest, context) -> bff.TurnOutcome:
        body = uc.RunTurn(
            text=request.text,
            # O padrão mora no modelo da borda, não aqui: um segundo padrão no
            # adaptador é como as duas portas passam a rotear diferente.
            task_kind=request.task_kind or "implementation",
            provider=request.provider,
            operator_note=request.operator_note,
            max_output_tokens=request.max_output_tokens or 8192,
        )
        try:
            resultado = await uc.run_turn(
                request.demand_id, request.thread_id, body, request.idempotency_key
            )
        except AgentProviderUnavailable as exc:
            get_logger().warning(
                "provedor de agente indisponível",
                provider=exc.provider,
                reason=exc.reason.value,
                error=exc.debug_detail,
            )
            # UNAVAILABLE e não INTERNAL: o cliente precisa distinguir "o
            # fornecedor de IA está fora" de "o BFF quebrou".
            await context.abort(grpc.StatusCode.UNAVAILABLE, exc.message)
        return _outcome(resultado)


__all__ = ["RuntimeServicer"]
