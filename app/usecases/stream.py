"""Casos de uso de streaming — o que está acontecendo AGORA, nas duas portas.

Mesma disciplina de `identity` e `hierarchy`: a regra vive aqui, adaptadores
traduzem. Ver o docstring de `app/usecases/identity.py` para o porquê dos
decorators morarem no caso de uso e não no router.

O que este módulo converte: os três RPCs de *server-streaming* do núcleo
(`EventService.WatchEvents`, `DemandService.WatchDemand`,
`ExecutionService.StreamLogs`) em geradores assíncronos de modelos da borda.
`app/routers/stream.py` os embrulha em SSE; `app/grpcapi/stream.py` os reemite
como stream gRPC. Nenhum dos dois decide nada.

Três decisões estruturais, porque stream tem armadilhas que unário não tem:

1. **Sem deadline.** Todo caso de uso unário manda `timeout=core_deadline_s`.
   Aqui não: uma assinatura saudável dura horas, e um deadline a mataria no
   meio por definição. Quem termina o stream é o cliente indo embora, o núcleo
   fechando, ou um erro.

2. **Cancelamento é obrigação nossa.** Se o consumidor para de iterar (o
   browser fechou a aba, o CLI deu Ctrl-C), o gerador é finalizado e o `finally`
   CANCELA a chamada gRPC. Sem isso a assinatura continua viva do outro lado da
   rede — o `ctx.Done()` do núcleo nunca dispara e o watcher dele fica no
   fan-out para sempre. É vazamento de goroutine causado por descuido daqui.

3. **@account_scoped vale na ABERTURA.** O decorator não é assíncrono para
   função geradora, então a checagem roda quando o gerador é CRIADO — antes do
   primeiro item. É exatamente o que se quer: stream não pode ser a porta que
   nasce aberta, e recusar só na primeira iteração já seria tarde (o SSE já
   teria respondido 200).

`@log` de propósito NÃO é usado: ele mede a duração de uma *chamada*, e uma
chamada que devolve um gerador dura microssegundos. O que interessa num stream
é a duração da CONEXÃO e quantos itens saíram — registrados à mão no `finally`.
"""

import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime

from google.protobuf import json_format
from pydantic import BaseModel, Field

from app.coreclient import stubs
from app.coreclient.client import core
from app.coreclient.convert import call_context_from
from app.coreclient.gen.dop.v1 import demand_pb2, event_pb2, execution_pb2
from app.platform.context import auth_ctx
from app.platform.logging.config import FIELD_DURATION_MS, get_logger
from app.platform.security.decorator import account_scoped

# ── modelos da borda ────────────────────────────────────────────────────────


class StreamEvent(BaseModel):
    """Um evento do log, já na forma que as duas portas emitem.

    `payload` é dict (não Struct) de propósito: é o formato comum entre o JSON
    do SSE e o protobuf do gRPC, e é o que garante que os dois adaptadores
    rendam a MESMA coisa em vez de cada um traduzir o Struct à sua maneira.
    """

    id: str = ""
    type: str = ""
    aggregate: str = ""
    aggregate_id: str = ""
    payload: dict = Field(default_factory=dict)
    occurred_at: datetime | None = None


class LogLine(BaseModel):
    source: str = ""
    service: str = ""
    line: str = ""
    at: datetime | None = None


# ── tradução do núcleo para a borda ─────────────────────────────────────────


def _quando(msg, campo: str) -> datetime | None:
    """Timestamp ausente vira None, não a época zero.

    1970-01-01 numa timeline é pior que nada: aparece como evento antiquíssimo
    no topo da tela em vez de aparecer como o que é — sem horário.
    """
    if not msg.HasField(campo):
        return None
    return getattr(msg, campo).ToDatetime(tzinfo=UTC)


def _payload(msg, campo: str = "payload") -> dict:
    # Struct → dict via json_format: é a conversão canônica do protobuf, e
    # preserva número, booleano e aninhamento sem tabela nossa no meio.
    return json_format.MessageToDict(getattr(msg, campo)) if msg.HasField(campo) else {}


def _evento(env: event_pb2.EventEnvelope) -> StreamEvent:
    return StreamEvent(
        id=env.id,
        type=env.type,
        aggregate=env.aggregate,
        aggregate_id=env.aggregate_id,
        payload=_payload(env),
        occurred_at=_quando(env, "occurred_at"),
    )


def _evento_de_demanda(ev: demand_pb2.DemandEvent, demand_id: str) -> StreamEvent:
    """DemandEvent do núcleo não tem id nem aggregate_id.

    O `id` fica VAZIO em vez de inventado: é ele que o SSE emite como `id:` e
    que o cliente devolveria como cursor de retomada. Um id fabricado aqui
    prometeria uma retomada que `WatchDemand` não sabe cumprir — o contrato do
    núcleo não tem `since_event_id` para esse RPC. O `aggregate_id` é a própria
    demanda, que o chamador já informou.
    """
    return StreamEvent(
        id="",
        type=ev.type,
        aggregate=ev.aggregate or "demand",
        aggregate_id=demand_id,
        payload=_payload(ev),
        occurred_at=_quando(ev, "at"),
    )


def _linha(ll: execution_pb2.LogLine) -> LogLine:
    return LogLine(
        source=ll.source, service=ll.service, line=ll.line, at=_quando(ll, "at")
    )


# ── a mecânica comum dos três streams ───────────────────────────────────────


async def _bombear(chamada, traduz, *, rotulo: str, **campos_de_log) -> AsyncIterator:
    """Itera a chamada de streaming do núcleo e garante o cancelamento.

    O `finally` é o coração deste módulo. Ele roda tanto no fim natural quanto
    quando o consumidor abandona o gerador (aba fechada, Ctrl-C, erro no
    adaptador): nos dois casos a chamada gRPC é cancelada e o núcleo vê o
    contexto encerrar. Sem ele, cliente sumindo vira assinatura órfã lá.
    """
    inicio = time.perf_counter()
    emitidos = 0
    get_logger().info("stream aberto", stream=rotulo, **campos_de_log)
    try:
        async for msg in chamada:
            emitidos += 1
            yield traduz(msg)
    finally:
        # `cancel()` é idempotente e devolve False se a chamada já terminou —
        # chamar sempre é mais barato que descobrir se precisa.
        chamada.cancel()
        get_logger().info(
            "stream encerrado",
            stream=rotulo,
            emitted=emitidos,
            **campos_de_log,
            **{FIELD_DURATION_MS: round((time.perf_counter() - inicio) * 1000)},
        )


# ── casos de uso ────────────────────────────────────────────────────────────


@account_scoped
def watch_account_events(
    *,
    since_event_id: str = "",
    aggregate: list[str] | None = None,
    types: list[str] | None = None,
) -> AsyncIterator[StreamEvent]:
    """Eventos da conta ativa — o que alimenta timeline e caixa de atenção.

    `since_event_id` é o cursor de retomada e atravessa INTACTO para o núcleo:
    é ele quem drena o log a partir dali e emenda no fluxo ao vivo sem buraco e
    sem duplicata. A borda não guarda posição de leitura de ninguém — se
    guardasse, teria estado, e o BFF não tem estado (ADR-0016).

    Função comum (não `async def`) devolvendo o gerador: assim o
    `@account_scoped` recusa no ato da abertura, e não na primeira iteração.
    """
    ctx = auth_ctx.get()
    chamada = stubs.event_stub().WatchEvents(
        event_pb2.WatchEventsRequest(
            ctx=call_context_from(ctx),
            aggregate=aggregate or [],
            types=types or [],
            since_event_id=since_event_id,
        ),
        metadata=core.metadata(),
    )
    return _bombear(
        chamada, _evento, rotulo="account_events", since_event_id=since_event_id
    )


@account_scoped
def watch_demand(demand_id: str) -> AsyncIterator[StreamEvent]:
    """Eventos de uma demanda — mensagem de thread, etapa, achado publicado."""
    ctx = auth_ctx.get()
    chamada = stubs.demand_stub().WatchDemand(
        demand_pb2.WatchDemandRequest(ctx=call_context_from(ctx), demand_id=demand_id),
        metadata=core.metadata(),
    )
    return _bombear(
        chamada,
        lambda ev: _evento_de_demanda(ev, demand_id),
        rotulo="demand",
        demand_id=demand_id,
    )


@account_scoped
def tail_sandbox_logs(
    sandbox_id: str, *, source: str = "", service: str = "", test_type: str = ""
) -> AsyncIterator[LogLine]:
    """Cauda de log de um sandbox. Os filtros passam adiante sem interpretação."""
    ctx = auth_ctx.get()
    chamada = stubs.execution_stub().StreamLogs(
        execution_pb2.StreamLogsRequest(
            ctx=call_context_from(ctx),
            sandbox_id=sandbox_id,
            source=source,
            service=service,
            test_type=test_type,
        ),
        metadata=core.metadata(),
    )
    return _bombear(chamada, _linha, rotulo="sandbox_logs", sandbox_id=sandbox_id)
