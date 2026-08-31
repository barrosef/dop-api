"""Rotas do AgentRuntime — tradução HTTP do caso de uso, nada mais.

Zero decisão aqui, autorização inclusa: os decorators estão no caso de uso, e é
por isso que a porta gRPC nasce com as mesmas regras.

A ÚNICA coisa que este arquivo decide é o código HTTP da indisponibilidade de
terceiro, e ela merece o parágrafo:

**Provedor de agente fora do ar não é 500.** Um 500 diz "o BFF quebrou" e manda
o time errado investigar; e para o usuário significa esperar por um conserto
que não existe. Sem chave, sem rede, modelo fora do catálogo do fornecedor,
cota estourada do lado dele — nada disso é defeito nosso. Sai como **502 Bad
Gateway**, com `provider` e `reason` legíveis por máquina no corpo, para que o
cliente consiga distinguir "reconfigure a credencial" de "tente de novo mais
tarde" sem ler texto em português.

O detalhe cru do SDK do fornecedor NÃO entra na resposta (ele carrega URL,
cabeçalho e às vezes prefixo de chave): fica no log, onde o mascaramento
automático de `app/platform/logging/config.py` ainda passa por cima.
"""

from fastapi import APIRouter, Header, HTTPException

from app.platform.logging.config import get_logger
from app.runtime.errors import AgentProviderUnavailable
from app.usecases import runtime as uc
from app.usecases.runtime import (
    FindingRef,
    RoutingView,
    RunTurn,
    TurnOutcome,
    TurnUsage,
)

router = APIRouter(prefix="/api/v1/runtime", tags=["runtime"])

__all__ = [
    "FindingRef",
    "RoutingView",
    "RunTurn",
    "TurnOutcome",
    "TurnUsage",
    "router",
]

#: 502, e não 503: 503 é o que a tradução do núcleo já usa para UNAVAILABLE do
#: dop-core (ver `app/platform/errors.py`). Reusar o mesmo código faria "o
#: núcleo caiu" e "o fornecedor de IA caiu" chegarem indistinguíveis ao cliente
#: — que são exatamente os dois casos que ele precisa separar.
HTTP_PROVEDOR_INDISPONIVEL = 502


def _como_http(exc: AgentProviderUnavailable) -> HTTPException:
    """Indisponibilidade do provedor → 502 com corpo legível por máquina."""
    get_logger().warning(
        "provedor de agente indisponível",
        provider=exc.provider,
        reason=exc.reason.value,
        # Só aqui, nunca na resposta.
        error=exc.debug_detail,
    )
    return HTTPException(
        status_code=HTTP_PROVEDOR_INDISPONIVEL,
        detail={
            "kind": "agent_provider_unavailable",
            "provider": exc.provider,
            "reason": exc.reason.value,
            "detail": exc.message,
            "guidance": exc.guidance,
        },
    )


@router.post("/demands/{demand_id}/threads/{thread_id}/turns", response_model=TurnOutcome)
async def run_turn(
    demand_id: str,
    thread_id: str,
    body: RunTurn,
    idempotency_key: str = Header(default="", alias="Idempotency-Key"),
) -> TurnOutcome:
    """Executa um turno do agente nesta thread.

    **Responde 200 mesmo quando o orçamento estoura.** O corte da ADR-0011 §2 é
    suave: a demanda pausa e vira item da caixa de atenção, e o turno que já
    rodou vem inteiro — a resposta foi publicada na thread e o achado também.
    `paused`, `notice` e `budgets` trazem o que o humano precisa para decidir.

    **O acompanhamento ao vivo não sai por aqui.** As mensagens publicadas são
    eventos (ADR-0006) e chegam pelo SSE que já existe
    (`GET /api/v1/stream/demands/{demand_id}`). Esta rota devolve o resultado
    consolidado do turno; um segundo caminho de streaming seria uma segunda
    fonte da verdade para a mesma timeline.

    **`Idempotency-Key` não é opcional na prática.** Todas as escritas do turno
    derivam dela; sem ela o BFF gera uma, e aí cada chamada é um turno NOVO —
    é o cliente quem sabe se está retentando ou perguntando de novo.
    """
    try:
        return await uc.run_turn(demand_id, thread_id, body, idempotency_key)
    except AgentProviderUnavailable as exc:
        raise _como_http(exc) from exc
