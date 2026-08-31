"""Servicer gRPC da caixa de atenção — adaptador protobuf sobre os casos de uso.

Simétrico a `app/routers/attention.py`: recebe mensagem, chama a MESMA função de
`app/usecases/attention.py`, devolve mensagem. Nenhuma decisão aqui — nem
autorização, nem chamada ao núcleo, nem o agrupamento por demanda (esse vem
pronto do caso de uso, senão as duas portas agrupariam de jeitos diferentes e a
mesma fila apareceria em duas ordens).

Por que a porta gRPC também oferece a caixa, em vez de mandar todo mundo usar
REST+SSE: o `dop-cli` já fala gRPC e já carrega o token no metadado, e "onde eu
sou necessário agora" é exatamente a pergunta que se faz do terminal. Obrigá-lo
a implementar `text/event-stream` para acompanhar a mesma fila seria pedir um
segundo cliente para o mesmo dado.

`WatchAttention` é uma função GERADORA. Não é estilo: o `grpc.aio` escolhe como
executar o handler a partir disso — ver o docstring de `interceptors.py`.
"""

from app.grpcapi.gen.dop.bff.v1 import attention_pb2 as bff
from app.grpcapi.gen.dop.bff.v1 import attention_pb2_grpc as bff_grpc
from app.usecases import attention as uc

# Nome ↔ enum na direção da saída. A tabela do caso de uso vai do enum do NÚCLEO
# para o nome; esta vai do nome para o enum da BORDA. São dois contratos
# diferentes, e um dicionário só (invertido na hora) esconderia isso.
ENUM_POR_KIND: dict[str, int] = {
    "thread_blocked": bff.ATTENTION_KIND_THREAD_BLOCKED,
    "gate_pending": bff.ATTENTION_KIND_GATE_PENDING,
    "pr_review": bff.ATTENTION_KIND_PR_REVIEW,
    "merge_conflict": bff.ATTENTION_KIND_MERGE_CONFLICT,
    "directive": bff.ATTENTION_KIND_DIRECTIVE,
    "budget_exceeded": bff.ATTENTION_KIND_BUDGET_EXCEEDED,
    "integration_broken": bff.ATTENTION_KIND_INTEGRATION_BROKEN,
}

ENUM_POR_CHANGE: dict[str, int] = {
    "opened": bff.ATTENTION_CHANGE_OPENED,
    "resolved": bff.ATTENTION_CHANGE_RESOLVED,
}


def _item(i: uc.AttentionItem) -> bff.AttentionItem:
    msg = bff.AttentionItem(
        id=i.id,
        kind=ENUM_POR_KIND.get(i.kind, bff.ATTENTION_KIND_UNSPECIFIED),
        target_kind=i.target_kind,
        target_id=i.target_id,
        demand_id=i.demand_id,
        title=i.title,
        summary=i.summary,
        priority=i.priority,
    )
    # Só preenche o que existe: um Timestamp zerado diria "aberto em 1970", e do
    # outro lado o HasField responderia que há data.
    if i.opened_at is not None:
        msg.opened_at.FromDatetime(i.opened_at)
    if i.resolved_at is not None:
        msg.resolved_at.FromDatetime(i.resolved_at)
    return msg


class AttentionServicer(bff_grpc.AttentionServiceServicer):
    async def ListAttention(
        self, request: bff.ListAttentionRequest, context
    ) -> bff.AttentionBox:
        caixa = await uc.list_attention(
            request.include_resolved, request.demand_id, request.page_size
        )
        return bff.AttentionBox(
            items=[_item(i) for i in caixa.items],
            groups=[
                bff.AttentionGroup(
                    demand_id=g.demand_id, items=[_item(i) for i in g.items]
                )
                for g in caixa.groups
            ],
            open_total=caixa.open_total,
        )

    async def WatchAttention(self, request: bff.WatchAttentionRequest, context):
        # O caso de uso é chamado ANTES do primeiro `yield`, e é essa chamada
        # que dispara o @account_scoped — recusa sai como status, sem o cliente
        # ter recebido um stream vazio que parece sucesso.
        fonte = uc.watch_attention(since_event_id=request.since_event_id)
        async for atualizacao in fonte:
            yield bff.AttentionUpdate(
                change=ENUM_POR_CHANGE.get(
                    atualizacao.change, bff.ATTENTION_CHANGE_UNSPECIFIED
                ),
                item=_item(atualizacao.item),
            )
