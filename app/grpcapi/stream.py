"""Servicer gRPC de streaming — adaptador protobuf sobre os casos de uso.

Simétrico a `app/routers/stream.py`: recebe mensagem, itera a MESMA função de
`app/usecases/stream.py`, emite mensagem. Nenhuma decisão aqui — nem
autorização, nem chamada ao núcleo.

Por que a porta gRPC também oferece isto, em vez de mandar todo mundo usar SSE:
o `dop-cli` e os agentes dos sandboxes já falam gRPC e já carregam o token no
metadado. Obrigá-los a implementar `text/event-stream` (com reconexão,
`Last-Event-ID` e parsing de texto) para acompanhar exatamente os mesmos
eventos seria pedir um segundo cliente para o mesmo dado.

Duas diferenças em relação ao SSE, e as duas são do TRANSPORTE, não da regra:

* **Erro no meio do stream.** Aqui ele sobe como exceção e o `ErrorInterceptor`
  o transforma em status — gRPC carrega status nos trailers, então dá para
  falhar depois do primeiro item. No SSE não dá (o 200 já foi), e por isso lá o
  erro vira um evento `error`.
* **Heartbeat.** Não existe `: ping` aqui: HTTP/2 tem keepalive próprio, e o
  canal do BFF já o configura (`grpc.keepalive_time_ms`). Inventar um evento de
  ping poluiria o stream com item que não é evento.

Todo método é uma função GERADORA. Não é estilo: o `grpc.aio` escolhe como
executar o handler a partir disso — ver o docstring de `interceptors.py`.
"""

from app.grpcapi.gen.dop.bff.v1 import stream_pb2 as bff
from app.grpcapi.gen.dop.bff.v1 import stream_pb2_grpc as bff_grpc
from app.usecases import stream as uc


def _evento(e: uc.StreamEvent) -> bff.StreamEvent:
    msg = bff.StreamEvent(
        id=e.id, type=e.type, aggregate=e.aggregate, aggregate_id=e.aggregate_id
    )
    # Só preenche o que existe: campo de mensagem ausente e zerado são coisas
    # diferentes, e um payload vazio atribuído inventaria conteúdo onde não há.
    if e.payload:
        msg.payload.update(e.payload)
    if e.occurred_at is not None:
        msg.occurred_at.FromDatetime(e.occurred_at)
    return msg


def _linha(ll: uc.LogLine) -> bff.LogLine:
    msg = bff.LogLine(source=ll.source, service=ll.service, line=ll.line)
    if ll.at is not None:
        msg.at.FromDatetime(ll.at)
    return msg


class StreamServicer(bff_grpc.StreamServiceServicer):
    async def WatchEvents(self, request: bff.WatchEventsRequest, context):
        # O caso de uso é chamado ANTES do primeiro `yield`, e é essa chamada
        # que dispara o @account_scoped — recusa sai como status, sem o cliente
        # ter recebido um stream vazio que parece sucesso.
        fonte = uc.watch_account_events(
            since_event_id=request.since_event_id,
            aggregate=list(request.aggregate),
            types=list(request.types),
        )
        async for evento in fonte:
            yield _evento(evento)

    async def WatchDemand(self, request: bff.WatchDemandRequest, context):
        async for evento in uc.watch_demand(request.demand_id):
            yield _evento(evento)

    async def TailLogs(self, request: bff.TailLogsRequest, context):
        fonte = uc.tail_sandbox_logs(
            request.sandbox_id,
            source=request.source,
            service=request.service,
            test_type=request.test_type,
        )
        async for linha in fonte:
            yield _linha(linha)
