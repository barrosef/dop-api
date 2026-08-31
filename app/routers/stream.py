"""Rotas SSE — o streaming gRPC do núcleo convertido para o browser.

A ADR-0017 (convenção 1) manda: *streaming server-side para tudo ao vivo; o BFF
converte em SSE para o browser*. Este módulo é essa conversão. Zero decisão de
negócio aqui — a regra está em `app/usecases/stream.py`.

Cinco pontos decidem se um endpoint SSE é bom ou é uma fonte de bug
intermitente. Cada um está resolvido abaixo, com o porquê:

**Retomada.** O protocolo SSE já tem o mecanismo (`Last-Event-ID`) e o núcleo já
tem a contraparte (`since_event_id`). Ligar os dois é só isso: emitir `id:` com
o id do evento DO NÚCLEO — não um contador nosso, que não significaria nada do
outro lado — e devolver o `Last-Event-ID` recebido como cursor. O EventSource
reenvia esse cabeçalho sozinho a cada reconexão, então o browser que caiu e
voltou não perde evento nem recebe duplicado: quem garante isso é o replay do
núcleo, e a única coisa que a borda precisa fazer é não quebrar a corrente.

**Cliente que some.** Tratado no caso de uso (`_bombear`): quando o
EventSourceResponse detecta `http.disconnect`, cancela a tarefa que consome o
gerador, o `finally` do gerador roda e a chamada gRPC é cancelada. A assinatura
morre junto com a aba.

**Consumidor lento.** O núcleo derruba o assinante lento com `UNAVAILABLE` e uma
mensagem pedindo para reconectar com `since_event_id`. Isso vira um evento SSE
`error` com `retryable: true` e o último id emitido — em vez de um stream que
morre em silêncio e deixa o cockpit mostrando dados velhos como se fossem novos.

**Erro depois do primeiro byte.** Uma vez enviado o `200 OK`, não existe trocar
por 500: o status já foi para o fio. Por isso o gerador NUNCA deixa exceção
escapar depois de aberto — ela vira `event: error`, com o detalhe passado pelo
MESMO redator do resto da borda (`_detail_for`), que não deixa mensagem de 5xx
do núcleo vazar. Erro ANTES do primeiro byte (sem token, sem conta ativa) segue
sendo status HTTP normal: `@account_scoped` roda quando o caso de uso é
chamado, dentro do handler, antes de a resposta começar.

**Heartbeat.** `: ping` periódico, intervalo em `settings.sse_ping_s` (o porquê
do número está lá).
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

# Nomes de evento SSE: um conjunto PEQUENO e estável, não o vocabulário de
# `type` do núcleo.
#
# Tentador seria emitir `event: dop.hierarchy.project.created`. Mas aí o cockpit
# precisaria de um addEventListener por tipo de evento existente, e passaria a
# quebrar (silenciosamente: o evento simplesmente não chega) toda vez que o
# núcleo criasse um tipo novo. Com três nomes fixos, o cliente escuta três
# coisas e filtra por `type` dentro do JSON — que é dado, não protocolo.
EVENTO = "event"
LOG = "log"
ERRO = "error"

# Códigos que valem tentar de novo. O caso que importa é o UNAVAILABLE do
# assinante lento: o núcleo derruba de propósito e espera reconexão com cursor.
_RETENTAVEIS = frozenset(
    {
        StatusCode.UNAVAILABLE,
        StatusCode.DEADLINE_EXCEEDED,
        StatusCode.RESOURCE_EXHAUSTED,
        StatusCode.ABORTED,
    }
)

# Texto NOSSO, para quando o redator manda calar a boca do núcleo.
#
# Tensão real, e vale explicar: a mensagem do assinante lento ("reconecte com
# since_event_id do último evento recebido") é justamente a que o cockpit
# gostaria de mostrar — e ela vem num UNAVAILABLE, que é 503, que o `_detail_for`
# reduz a "erro interno". A regra dele está certa e não se afrouxa por
# conveniência: detalhe de 5xx do núcleo pode carregar host, query ou credencial,
# e ninguém quer descobrir isso pela tela do usuário. A saída é não repetir a
# frase do núcleo, e sim escrever a nossa — que não carrega dado nenhum de lá.
# O que o cliente precisa para agir continua nos campos estruturados (`code`,
# `retryable`, `since_event_id`), que são de máquina e não são redigidos.
_RECONECTE = "conexão encerrada pelo núcleo; reconecte com since_event_id"


def _json(dados: dict) -> str:
    # `default=str` cobre o datetime dos modelos sem uma tabela de serialização
    # nossa. ensure_ascii desligado porque o payload carrega texto em português.
    return json.dumps(dados, ensure_ascii=False, default=str)


def _abertura() -> ServerSentEvent:
    """Primeiro quadro do stream: só o `retry:`.

    Evento sem `data:` não é despachado pelo EventSource — ele apenas absorve o
    campo `retry`. É o jeito de configurar o intervalo de reconexão do cliente
    sem inventar um evento falso que o cockpit teria de aprender a ignorar.
    """
    return ServerSentEvent(retry=settings.sse_retry_ms)


def _erro(exc: AioRpcError, ultimo_id: str) -> ServerSentEvent:
    """Erro do núcleo, no meio do stream, como evento SSE.

    O detalhe passa pelo `_detail_for` que o REST e o gRPC já usam — 5xx do
    núcleo não vaza por porta nenhuma, e não há um segundo redator para
    envelhecer em separado. Quando ele cala e o erro é retentável, entra o
    `_RECONECTE`, que é texto nosso (ver o comentário da constante).

    `since_event_id` vai no corpo mesmo já tendo sido emitido como `id:`: o
    cliente que reconecta com `EventSource` usa o cabeçalho automático, mas quem
    fala com este endpoint por `fetch` (ou quem recarregou a página) precisa do
    cursor em algum lugar que consiga ler.
    """
    status = http_status_for(exc.code())
    retentavel = exc.code() in _RETENTAVEIS
    detalhe = _detail_for(exc, status)
    if retentavel and status >= 500:
        detalhe = _RECONECTE
    return ServerSentEvent(
        event=ERRO,
        data=_json(
            {
                "status": status,
                "code": exc.code().name,
                "detail": detalhe,
                # Falso NÃO significa "pare de tentar" para o EventSource, que
                # reconecta sozinho de qualquer jeito — significa que o cliente
                # deve fechar a conexão em vez de insistir. É por isso que o
                # cockpit precisa deste campo e não só do fim do stream.
                "retryable": retentavel,
                "since_event_id": ultimo_id,
            }
        ),
    )


async def _eventos_sse(fonte, *, nome: str):
    """Traduz o gerador do caso de uso em quadros SSE, sem deixar erro escapar.

    Depois do `yield _abertura()` o status 200 já foi para o fio. A partir daí,
    exceção nenhuma pode subir: subir viraria um stream cortado no meio, que do
    lado do browser é indistinguível de rede ruim. Vira `event: error`.
    """
    ultimo_id = ""
    yield _abertura()
    try:
        async for item in fonte:
            dados = item.model_dump()
            # `id:` só quando existe. Emitir vazio faria o browser mandar um
            # Last-Event-ID vazio na reconexão — indistinguível de "nunca vi
            # nada" — e o stream de demanda, que não tem cursor, passaria a
            # mentir que tem.
            evento_id = dados.get("id") or None
            if evento_id:
                ultimo_id = evento_id
            yield ServerSentEvent(event=nome, id=evento_id, data=_json(dados))
    except AioRpcError as exc:
        get_logger().warning(
            "stream interrompido pelo núcleo", code=str(exc.code()), stream=nome
        )
        yield _erro(exc, ultimo_id)
    except Exception as exc:  # noqa: BLE001 - ver comentário
        # Falha nossa. Mesmo tratamento do ErrorInterceptor: inteira no log,
        # genérica na rede. Deixar propagar cortaria a resposta pela metade.
        get_logger().error("erro não tratado no stream", error=str(exc), stream=nome)
        yield ServerSentEvent(
            event=ERRO,
            data=_json(
                {
                    "status": 500,
                    "code": "INTERNAL",
                    "detail": "erro interno",
                    "retryable": False,
                    "since_event_id": ultimo_id,
                }
            ),
        )


def _resposta(fonte, *, nome: str) -> EventSourceResponse:
    return EventSourceResponse(
        _eventos_sse(fonte, nome=nome), ping=settings.sse_ping_s
    )


def _cursor(last_event_id: str, since_event_id: str) -> str:
    """Cabeçalho vence parâmetro de consulta, e a ordem não é arbitrária.

    O `Last-Event-ID` é REENVIADO pelo browser a cada reconexão automática, com
    o id mais recente que ele processou. A URL, essa fica congelada no momento
    em que o EventSource foi criado — o `?since_event_id=` dela envelhece na
    primeira reconexão. Preferir a query traria eventos já vistos de volta a
    cada queda de rede.

    O parâmetro continua existindo porque o EventSource não deixa o cliente
    definir cabeçalho: na PRIMEIRA conexão depois de um F5, o cursor guardado
    pelo cockpit só tem esse caminho para chegar aqui.
    """
    return last_event_id or since_event_id


@router.get("/events")
async def stream_account_events(
    aggregate: Annotated[list[str] | None, Query()] = None,
    types: Annotated[list[str] | None, Query()] = None,
    since_event_id: str = "",
    last_event_id: str = Header(default="", alias="Last-Event-ID"),
) -> EventSourceResponse:
    """Eventos da conta ativa — timeline e caixa de atenção do cockpit.

    O caso de uso é chamado AQUI, fora do gerador, de propósito: é essa chamada
    que dispara `@account_scoped`. Recusa sem conta ativa sai como 400 de
    verdade, com corpo JSON, e não como um 200 que morre no primeiro quadro.
    """
    fonte = uc.watch_account_events(
        since_event_id=_cursor(last_event_id, since_event_id),
        aggregate=aggregate or [],
        types=types or [],
    )
    return _resposta(fonte, nome=EVENTO)


@router.get("/demands/{demand_id}")
async def stream_demand(demand_id: str) -> EventSourceResponse:
    """Eventos de uma demanda — o que faz o chat e a timeline dela viverem.

    Sem cursor: `dop.v1.WatchDemandRequest` não tem `since_event_id`, então este
    stream não aceita `Last-Event-ID` — e, coerentemente, não emite `id:`.
    Fingir retomada aqui daria ao cockpit a impressão de que nada se perdeu numa
    reconexão. Quem reconecta relê o dossiê da demanda.
    """
    return _resposta(uc.watch_demand(demand_id), nome=EVENTO)


@router.get("/sandboxes/{sandbox_id}/logs")
async def stream_sandbox_logs(
    sandbox_id: str,
    source: str = "",
    service: str = "",
    test_type: str = "",
) -> EventSourceResponse:
    """Cauda de log de um sandbox. Também sem cursor, pela mesma razão."""
    fonte = uc.tail_sandbox_logs(
        sandbox_id, source=source, service=service, test_type=test_type
    )
    return _resposta(fonte, nome=LOG)
