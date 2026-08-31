"""Rota de execução de turno — tradução HTTP, nada mais.

O runtime NÃO vive no BFF (ADR-0023): esta rota chama o núcleo, que é quem lê
a credencial do provedor, do cofre, no mesmo processo.
"""

from fastapi import APIRouter, Header

from app.usecases import runtime as uc
from app.usecases.runtime import RunTurn, TurnOutcome

router = APIRouter(prefix="/api/v1", tags=["runtime"])

__all__ = ["RunTurn", "TurnOutcome", "router"]


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
    `paused` e `notice` trazem o que o humano precisa para decidir.

    **O acompanhamento ao vivo não sai por aqui.** As mensagens publicadas são
    eventos (ADR-0006) e chegam pelo SSE que já existe
    (`GET /api/v1/stream/demands/{demand_id}`). Esta rota devolve o resultado
    consolidado; um segundo caminho de streaming seria uma segunda fonte da
    verdade para a mesma timeline.

    **`Idempotency-Key` é obrigatória**, e a borda NÃO gera uma. Turno de
    agente gasta dinheiro: uma chave inventada aqui transformaria retry de rede
    em consumo em dobro sem ninguém pedir. É o cliente quem sabe se está
    retentando ou perguntando de novo.

    Provedor indisponível, credencial recusada e conta sem integração de agente
    chegam do núcleo com o status certo, pelo tradutor de sempre — não há
    tratamento especial aqui, e não deve haver.
    """
    return await uc.run_turn(demand_id, thread_id, body, idempotency_key)
