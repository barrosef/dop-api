"""Casos de uso da caixa de atenção — a fila única de "onde eu sou necessário".

Mesma disciplina de `identity` e `hierarchy`: a regra vive aqui e só aqui;
`app/routers/attention.py` traduz HTTP/SSE e `app/grpcapi/attention.py` traduz
protobuf, os dois chamando estas MESMAS funções. Ver o docstring de
`app/usecases/identity.py` para o porquê de os decorators morarem no caso de uso
— autorização presa ao router deixaria a porta gRPC aberta.

A caixa é a fila entre TODAS as demandas da conta ativa, respondendo "onde eu
sou necessário, e em que ordem" (spec conversação-e-atenção §3). Não é o chat: é
o que leva ao chat certo. Cinco demandas com três threads cada são quinze
conversas, e sem esta fila o modelo multi-agente afoga o dev — os dois sobem
juntos ou nenhum sobe (risco R-2).

**Três coisas este módulo NÃO faz, e cada uma é uma decisão:**

1. **Não recalcula prioridade.** Ela é DERIVADA pelo núcleo — impacto do tipo,
   depois idade, com a idade desempatando só DENTRO da faixa
   (`internal/domain/attention/entity.go`). Uma segunda régua aqui faria a fila
   deixar de ter uma ordem só, que é exatamente o que ela existe para oferecer:
   uma pergunta exploratória de três dias passaria na frente de um conflito de
   produção de três minutos. `list_attention` preserva a ordem que o núcleo
   mandou, inclusive entre os grupos.

2. **Não conta o badge.** `open_total` vem do núcleo e conta a conta INTEIRA,
   independente da página e do filtro por demanda. Contar os itens da página
   daria um badge que muda quando o dev pagina — e um badge que diminui sozinho
   é um badge em que ninguém confia.

3. **Não fecha item.** Não existe "marcar como lido" nem "descartar", aqui nem
   no contrato da borda. A caixa é PROJEÇÃO (ADR-0006): item nasce de evento e
   morre de evento, e quem fecha é o FATO — o portão decidido, a thread
   destravada. Item que some sem o problema resolvido é mentira confortável, e
   caixa que mente vira caixa ignorada (risco R-1).

**O que ele faz que o núcleo não faz:** agrupa por demanda. A spec pede a caixa
agrupável por demanda, e quinze pendências numa lista plana não dizem "a demanda
3 está te esperando em três lugares". Agrupar é reordenar? Não: os itens de cada
grupo saem na ordem em que vieram, e os grupos saem na ordem da PRIMEIRA
ocorrência de cada demanda na fila — ou seja, na ordem do item mais urgente de
cada uma. Nenhum número novo é inventado no caminho.

O stream segue a mecânica de `app/usecases/stream.py`, importada e não copiada:
sem deadline, cancelamento no `finally`, `@account_scoped` valendo na abertura.
O porquê de cada uma das três está no docstring daquele módulo — repeti-las aqui
seria criar um segundo lugar para elas envelhecerem em separado.
"""

from collections.abc import AsyncIterator
from datetime import UTC, datetime

from pydantic import BaseModel, Field

from app.coreclient import stubs
from app.coreclient.client import core
from app.coreclient.convert import call_context_from
from app.coreclient.gen.dop.v1 import attention_pb2, common_pb2
from app.platform.context import auth_ctx
from app.platform.logging.decorator import log
from app.platform.security.decorator import account_scoped
from app.settings import settings
from app.usecases.stream import _bombear

# Nome ↔ enum num lugar só, como em `resource` e `knowledge`. O vocabulário é o
# do DOMÍNIO do núcleo (attention.Kind, em Go), e não uma tradução nossa: assim
# o que aparece na tela é a mesma palavra que aparece no log do núcleo quando
# alguém for investigar por que um item entrou na fila.
_KIND_POR_ENUM: dict[int, str] = {
    attention_pb2.AttentionItem.KIND_THREAD_BLOCKED: "thread_blocked",
    attention_pb2.AttentionItem.KIND_GATE_PENDING: "gate_pending",
    attention_pb2.AttentionItem.KIND_PR_REVIEW: "pr_review",
    attention_pb2.AttentionItem.KIND_MERGE_CONFLICT: "merge_conflict",
    attention_pb2.AttentionItem.KIND_DIRECTIVE: "directive",
    attention_pb2.AttentionItem.KIND_BUDGET_EXCEEDED: "budget_exceeded",
    attention_pb2.AttentionItem.KIND_INTEGRATION_BROKEN: "integration_broken",
}

_CHANGE_POR_ENUM: dict[int, str] = {
    attention_pb2.AttentionUpdate.CHANGE_OPENED: "opened",
    attention_pb2.AttentionUpdate.CHANGE_RESOLVED: "resolved",
}


def kind_nome(valor: int) -> str:
    return _KIND_POR_ENUM.get(valor, "")


def _deadline() -> float:
    return settings.core_deadline_s


# ── modelos da borda ────────────────────────────────────────────────────────


class AttentionItem(BaseModel):
    """Uma pendência que exige DECISÃO HUMANA.

    O que não exige decisão não entra: status e progresso ficam no cockpit.
    Caixa barulhenta vira ruído e é ignorada (risco R-1 da spec).
    """

    id: str = ""
    kind: str = ""
    # Onde clicar leva. O cockpit resolve a rota a partir do par; guardar a rota
    # pronta amarraria a borda ao desenho da tela.
    target_kind: str = ""
    target_id: str = ""
    # Vazio em item de CONTA (integração quebrada) — que não pertence a demanda
    # nenhuma. É por este campo que a caixa agrupa.
    demand_id: str = ""
    title: str = ""
    summary: str = ""
    # DERIVADA pelo núcleo. Menor número = mais urgente. Não se recalcula aqui.
    priority: int = 0
    # None = sem data, e não a época zero: 1970 numa fila ordenada por idade
    # apareceria como o item mais antigo do mundo.
    opened_at: datetime | None = None
    resolved_at: datetime | None = None


class AttentionGroup(BaseModel):
    """Os itens de uma demanda, na ordem em que o núcleo os deu."""

    # Vazio = os itens de CONTA. Grupo como os outros, e não um resto no fim:
    # integração quebrada para a conta inteira e costuma liderar a fila.
    demand_id: str = ""
    items: list[AttentionItem] = Field(default_factory=list)


class AttentionBox(BaseModel):
    items: list[AttentionItem] = Field(default_factory=list)
    groups: list[AttentionGroup] = Field(default_factory=list)
    # O BADGE, do núcleo: itens ABERTOS da conta inteira, independente desta
    # página e do filtro por demanda.
    open_total: int = 0


class AttentionUpdate(BaseModel):
    """Uma mudança na caixa — não a caixa inteira.

    `change` é a VERDADE do aviso. No `resolved`, o núcleo identifica o que
    fechou pelo ALVO (`kind` + `target_kind` + `target_id`), porque quem fecha
    conhece o alvo e não o id da projeção: o item chega sem `id`, sem título e
    sem datas. A borda repassa assim — inventar um id aqui, ou carimbar
    `resolved_at` com a hora do BFF, daria ao cockpit um dado que ninguém mediu.
    """

    change: str = ""
    # O id do EVENTO que gerou o aviso — a POSIÇÃO no log, não o id do item.
    #
    # É o cursor de retomada: o emissor SSE usa este campo como `id:`, e o
    # cliente o devolve em `since_event_id` ao reconectar. O id do ITEM não
    # serviria: ele não é posição no log, e mandá-lo de volta pediria ao núcleo
    # uma coisa que não existe.
    #
    # O núcleo passou a mandá-lo depois que esta borda foi escrita; enquanto
    # não mandava, o fluxo não emitia `id:` nenhum, o que era o certo — cursor
    # inventado é pior que cursor ausente.
    id: str = ""
    item: AttentionItem


# ── tradução do núcleo para a borda ─────────────────────────────────────────


def _quando(msg, campo: str) -> datetime | None:
    """Timestamp ausente vira None, não a época zero.

    Mesma regra de `usecases/stream.py`: HasField, porque timestamp é campo de
    MENSAGEM e ausente ≠ zerado.
    """
    if not msg.HasField(campo):
        return None
    return getattr(msg, campo).ToDatetime(tzinfo=UTC)


def _item(it: attention_pb2.AttentionItem) -> AttentionItem:
    return AttentionItem(
        id=it.id,
        kind=kind_nome(it.kind),
        target_kind=it.target_kind,
        target_id=it.target_id,
        # `demand` é mensagem opcional: item de conta não tem demanda, e ler
        # `it.demand.id` de um campo ausente devolveria "" — que é o que
        # queremos, mas por acidente. HasField diz a mesma coisa de propósito.
        demand_id=it.demand.id if it.HasField("demand") else "",
        title=it.title,
        summary=it.summary,
        priority=it.priority,
        opened_at=_quando(it, "opened_at"),
        resolved_at=_quando(it, "resolved_at"),
    )


def _agrupar(itens: list[AttentionItem]) -> list[AttentionGroup]:
    """Agrupa por demanda PRESERVANDO a ordem do núcleo.

    Duas ordens saem daqui, e nenhuma das duas é nova:

      - dentro do grupo, a ordem em que os itens chegaram;
      - entre grupos, a ordem da PRIMEIRA aparição de cada demanda na fila — ou
        seja, a do item mais urgente de cada uma.

    É por isso que o agrupamento não é uma segunda régua de prioridade: ele não
    compara nada. Um `sorted` por urgência do grupo daria o mesmo resultado hoje
    e passaria a divergir no dia em que a régua do núcleo mudasse — que é
    exatamente a divergência que este módulo existe para não ter.

    `dict` comum porque a inserção é ordenada desde o Python 3.7, e depender
    disso aqui é mais legível que carregar um OrderedDict para dizer o mesmo.
    """
    grupos: dict[str, AttentionGroup] = {}
    for item in itens:
        grupo = grupos.get(item.demand_id)
        if grupo is None:
            grupo = AttentionGroup(demand_id=item.demand_id)
            grupos[item.demand_id] = grupo
        grupo.items.append(item)
    return list(grupos.values())


# ── casos de uso ────────────────────────────────────────────────────────────


@log
@account_scoped
async def list_attention(
    include_resolved: bool = False, demand_id: str = "", page_size: int = 0
) -> AttentionBox:
    """A caixa da conta ativa: a fila, os grupos por demanda e o badge.

    Sem `page_token`: `dop.v1.AttentionService` não devolve cursor de página, e
    anunciar um na borda seria prometer uma navegação que a origem não sabe
    cumprir. Quem quer saber se está vendo a caixa inteira compara `open_total`
    com o tamanho de `items`.
    """
    ctx = auth_ctx.get()
    pedido = attention_pb2.ListAttentionRequest(
        ctx=call_context_from(ctx),
        include_resolved=include_resolved,
        page=common_pb2.PageRequest(size=page_size),
    )
    # Só preenche quando há filtro: um DemandRef vazio seria uma demanda de id
    # "", e o núcleo entenderia como filtro, não como ausência de filtro.
    if demand_id:
        pedido.demand.CopyFrom(common_pb2.DemandRef(id=demand_id))
    resp = await stubs.attention_stub().ListAttention(
        pedido, metadata=core.metadata(), timeout=_deadline()
    )
    itens = [_item(i) for i in resp.items]
    return AttentionBox(
        items=itens, groups=_agrupar(itens), open_total=resp.open_total
    )


@account_scoped
def watch_attention(since_event_id: str = "") -> AsyncIterator[AttentionUpdate]:
    """A caixa ao vivo: o que abriu e o que fechou, item a item.

    Função comum (não `async def`) devolvendo o gerador, e a mecânica vinda de
    `usecases.stream._bombear`: é o mesmo jeito de fazer stream que o resto da
    borda usa, com o `@account_scoped` recusando no ato da ABERTURA e o
    cancelamento da chamada gRPC garantido no `finally`. Ver o docstring de
    `app/usecases/stream.py` para o porquê de cada uma das três decisões — este
    módulo não tem um segundo jeito de fazer a mesma coisa.

    `since_event_id` atravessa INTACTO para o núcleo, que drena o log a partir
    dali e emenda no fluxo ao vivo. A borda não guarda posição de leitura de
    ninguém — se guardasse, teria estado, e o BFF não tem estado (ADR-0016).
    """
    ctx = auth_ctx.get()
    chamada = stubs.attention_stub().WatchAttention(
        attention_pb2.WatchAttentionRequest(
            ctx=call_context_from(ctx), since_event_id=since_event_id
        ),
        metadata=core.metadata(),
    )
    return _bombear(
        chamada, _update, rotulo="attention", since_event_id=since_event_id
    )


def _update(u: attention_pb2.AttentionUpdate) -> AttentionUpdate:
    # `item` é campo de mensagem: um update sem item não vira item zerado. Não
    # deveria acontecer, e se acontecer o cliente vê um item vazio em vez de
    # receber um KeyError vindo de dentro do stream.
    item = _item(u.item) if u.HasField("item") else AttentionItem()
    return AttentionUpdate(
        change=_CHANGE_POR_ENUM.get(u.change, ""), item=item, id=u.event_id
    )
