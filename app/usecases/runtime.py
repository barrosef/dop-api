"""Execução de turno de agente — uma chamada fina ao núcleo.

**O runtime NÃO vive aqui** (ADR-0023). Ele vivia, e foi movido: o turno precisa
da credencial do provedor de agente, que mora no cofre, e o BFF é a camada
exposta à internet. Dar acesso ao cofre para esta camada significaria que
comprometê-la entregaria as credenciais de agente de TODAS as contas — e esta
plataforma já teve um bypass total de autenticação exatamente aqui.

No núcleo, a credencial sai do cofre e é usada no mesmo processo, sem atravessar
rede nenhuma. O que sobra para a borda é o que a borda deve fazer: autenticar,
traduzir e devolver.

O acompanhamento ao vivo continua pelo SSE que já existe: as mensagens do turno
viram eventos no núcleo e chegam sozinhas (`app/usecases/stream.py`). Não há um
segundo caminho de streaming, e não deve haver — seriam duas fontes da verdade.
"""

from pydantic import BaseModel, Field

from app.coreclient import stubs
from app.coreclient.client import core
from app.coreclient.convert import call_context_from
from app.coreclient.gen.dop.v1 import agent_pb2
from app.platform.context import auth_ctx
from app.platform.logging.decorator import log
from app.platform.security.decorator import account_scoped, require_role
from app.settings import settings
from app.usecases import cost as cost_uc


def _deadline() -> float:
    # Turno de agente é LONGO — chamada a modelo com raciocínio leva minutos.
    # O prazo do núcleo (10s por padrão) derrubaria todo turno real.
    return settings.agent_turn_deadline_s


class RunTurn(BaseModel):
    """Um turno a executar numa thread."""

    text: str = Field(min_length=1)
    # Vocabulário ABERTO (o núcleo trata desconhecido caindo no caro e diz que
    # caiu). Vazio é que não passa: sem tipo de trabalho não há roteamento.
    task_kind: str = Field(default="implementation", min_length=1)
    # Qual integração de agente usar. Vazio = a única da conta; havendo mais de
    # uma, o núcleo RECUSA com a lista em vez de escolher (ADR-0013).
    resource_id: str = ""
    # Instrução do OPERADOR, vinda da caixa de atenção. Entra pelo canal de
    # autoridade do fornecedor, nunca como texto de usuário.
    operator_note: str = ""
    max_output_tokens: int = Field(default=8192, ge=256, le=64000)


class TurnUsage(BaseModel):
    """As quatro parcelas, DISJUNTAS, mais o custo em micros inteiros."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    cost: cost_uc.Money = Field(default_factory=cost_uc.Money)
    # Falso = o provedor não reporta criação de cache. Zero afirmaria que nada
    # foi escrito no cache, que é outra coisa.
    cache_creation_known: bool = True
    # Falso = não há tabela de preço para este modelo. O custo NÃO vira zero de
    # consolo: orçamento alimentado com zeros é ficção.
    cost_known: bool = True


class RoutingView(BaseModel):
    """A decisão do núcleo, com a justificativa INTEIRA (ADR-0011 §3)."""

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
    achado = None
    # Ausente ≠ zerado: achado só existe quando a thread concluiu.
    if o.HasField("finding"):
        achado = FindingRef(id=o.finding.id, title=o.finding.title)
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
        finding=achado,
        usage=TurnUsage(
            input_tokens=u.input_tokens,
            output_tokens=u.output_tokens,
            cache_read_tokens=u.cache_read_tokens,
            cache_creation_tokens=u.cache_creation_tokens,
            cost=cost_uc.Money(
                currency=u.cost.currency, amount_micros=u.cost.amount_micros
            ),
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
    """Executa UM turno, no núcleo.

    A chave de idempotência é OBRIGATÓRIA no núcleo e a borda não inventa uma:
    turno de agente gasta dinheiro, e uma chave gerada aqui transformaria retry
    de rede em consumo em dobro sem ninguém pedir.
    """
    ctx = auth_ctx.get()
    o = await stubs.agent_stub().RunTurn(
        agent_pb2.RunTurnRequest(
            ctx=call_context_from(ctx),
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
