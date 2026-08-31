"""Rotas da caixa de atenção — tradução HTTP/SSE dos casos de uso, nada mais.

Zero decisão aqui: se aparecer um `if` de regra nesta camada, ele pertence a
`app/usecases/attention.py`, senão a porta gRPC fica sem ele.

**O SSE reusa `app/routers/stream.py`, e não reimplementa nada.** Aquele módulo
já resolveu os cinco pontos que decidem se um endpoint SSE é bom ou é fonte de
bug intermitente — retomada, cliente que some, consumidor lento, erro depois do
primeiro byte, heartbeat —, e cada um está explicado lá. Um segundo `async def`
gerando quadros SSE neste arquivo seria um segundo lugar para cada uma dessas
cinco coisas envelhecer em separado, e o dia em que as duas discordassem
ninguém saberia qual está certa. Daqui saem só três coisas: o nome do evento, o
cursor e a chamada do caso de uso.

**Por que um nome de evento novo (`attention`) e não o `event` de lá.** A regra
do `stream.py` é um conjunto PEQUENO e estável de nomes, para o cockpit não
precisar de um `addEventListener` por tipo do núcleo. `attention` entra por não
ser o mesmo dado: o corpo é um `AttentionUpdate` (uma mudança na fila), e não um
`StreamEvent` (um evento do log). São renderizadores diferentes na tela — a
mesma razão pela qual `log` já é separado de `event`. Reusar `event` obrigaria o
cockpit a farejar o formato do JSON para saber o que fazer com o quadro.

**Retomada, com uma ressalva honesta.** `dop.v1.WatchAttentionRequest` tem
`since_event_id`, então o cabeçalho `Last-Event-ID` e o parâmetro de consulta
são aceitos e atravessam, com a MESMA precedência do fluxo de eventos (o
cabeçalho vence — ver `stream._cursor`). O que não fecha o circuito é a volta:
`dop.v1.AttentionUpdate` não carrega o id do evento que a gerou, então não há
`id:` HONESTO para emitir, e o EventSource não tem o que reenviar sozinho.
Emitir o id do ITEM no lugar seria pior que não emitir: ele não é posição no
log, e voltaria ao núcleo como um cursor sem significado — exatamente o erro que
`stream.py` documenta ao explicar por que o `id:` é o id do evento DO NÚCLEO e
não um contador nosso. Enquanto o núcleo não publicar esse id, o cursor serve a
quem já sabe a posição do log (quem também assina `/stream/events`) e a quem o
guardou e o devolve por `?since_event_id=`.
"""

from fastapi import APIRouter, Header
from sse_starlette.sse import EventSourceResponse

from app.routers.stream import _cursor, _resposta
from app.usecases import attention as uc
from app.usecases.attention import (
    AttentionBox,
    AttentionGroup,
    AttentionItem,
    AttentionUpdate,
)

router = APIRouter(prefix="/api/v1", tags=["atenção"])

__all__ = [
    "AttentionBox",
    "AttentionGroup",
    "AttentionItem",
    "AttentionUpdate",
    "router",
]

# Nome do evento SSE desta fila — ver o docstring do módulo.
ATENCAO = "attention"


@router.get("/attention", response_model=AttentionBox)
async def list_attention(
    include_resolved: bool = False, demand_id: str = "", page_size: int = 0
) -> AttentionBox:
    """A caixa da conta ativa: a fila, os grupos por demanda e o badge.

    Não existe rota de "marcar como lido" nem de "descartar", e a ausência é a
    decisão: a caixa é projeção, item nasce e morre de evento, e item que some
    sem o problema resolvido é mentira confortável. Ver o caso de uso.
    """
    return await uc.list_attention(include_resolved, demand_id, page_size)


@router.get("/stream/attention")
async def stream_attention(
    since_event_id: str = "",
    last_event_id: str = Header(default="", alias="Last-Event-ID"),
) -> EventSourceResponse:
    """A caixa ao vivo — o que abriu e o que fechou.

    O caso de uso é chamado AQUI, fora do gerador, de propósito: é essa chamada
    que dispara o `@account_scoped`. Recusa sem conta ativa sai como 400 de
    verdade, com corpo JSON, e não como um 200 que morre no primeiro quadro.
    """
    fonte = uc.watch_attention(
        since_event_id=_cursor(last_event_id, since_event_id)
    )
    return _resposta(fonte, nome=ATENCAO)
