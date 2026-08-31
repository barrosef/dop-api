"""Casos de uso de conhecimento — regras, índice e memória (ADR-0009).

Mesma disciplina de `identity` e `hierarchy`: a regra vive aqui, router e
servicer traduzem. Ver o docstring de `app/usecases/identity.py` para o porquê
dos decorators morarem no caso de uso e não no adaptador.

**A regra que estrutura este módulo: o DESCARTE é informação de primeira
classe.** O pacote de contexto é selecionado por orçamento de tokens
(ADR-0012), e o que não coube não desaparece de fininho — a tela precisa poder
dizer "o contexto foi truncado". Daí `ContextPackageSummary.dropped` ser
`None`-ável e nunca zerado por conveniência: `None` é "o núcleo não informou",
zero é a AFIRMAÇÃO de que nada ficou de fora. São respostas diferentes e a
diferença é o ponto.

O outro trabalho de borda aqui é desfazer duas formas do núcleo que servem ao
banco e não à tela: as listas paralelas de `SearchMemory` viram pares, e as
chaves internas do `meta` (`dop.body`, `dop.scope`) viram campos.
"""

import base64

from google.protobuf import struct_pb2
from google.protobuf.json_format import MessageToDict
from pydantic import BaseModel, Field, field_validator

from app.coreclient import stubs
from app.coreclient.client import core
from app.coreclient.convert import call_context_from
from app.coreclient.gen.dop.v1 import common_pb2, knowledge_pb2
from app.platform.context import auth_ctx
from app.platform.logging.decorator import log
from app.platform.security.decorator import account_scoped
from app.settings import settings

# Nome ↔ enum num lugar só, como em `resource`. Espalhar esta tabela é como as
# duas pontas passam a discordar sobre o que é uma "memory".
_KIND_POR_NOME = {
    "rule": knowledge_pb2.KnowledgeArtifact.KIND_RULE,
    "index": knowledge_pb2.KnowledgeArtifact.KIND_INDEX,
    "memory": knowledge_pb2.KnowledgeArtifact.KIND_MEMORY,
}
_NOME_POR_KIND = {v: k for k, v in _KIND_POR_NOME.items()}

# Chaves NOSSAS que o núcleo esconde dentro do `meta` do artefato: o corpo do
# artefato pequeno e o nível de escopo. São convenção interna do núcleo — a
# borda as promove a campo e as REMOVE do meta, para que a tela nunca precise
# conhecer o prefixo "dop.".
_META_BODY = "dop.body"
_META_SCOPE = "dop.scope"

# As camadas do pacote de contexto (ADR-0009 §1), na ordem em que o orçamento
# de tokens as corta. São as chaves do `map<string,int32> dropped` do núcleo —
# escritas aqui uma vez, para que a tradução não dependa de ordem de iteração
# de mapa nem repita literal em quatro lugares.
_CAMADAS = ("rules", "findings", "index", "memories")


def _deadline() -> float:
    return settings.core_deadline_s


def _idempotency(chave: str = "") -> str:
    return chave or core.idempotency_key()


# ── modelos da borda ────────────────────────────────────────────────────────


class ArtifactSummary(BaseModel):
    id: str
    kind: str
    project_id: str = ""
    name: str
    version: int = 1
    # Preenchido só no artefato GRANDE, que o sandbox lê direto do storage.
    object_ref: str = ""
    # Meta do AUTOR, já sem as chaves internas do núcleo.
    meta: dict = Field(default_factory=dict)
    # Conteúdo inline do artefato pequeno.
    body: str = ""
    scope: str = ""


class DroppedCounts(BaseModel):
    """O que FICOU DE FORA do pacote, por camada (ADR-0012).

    `truncated` é derivado — é o único campo que a tela precisa consultar para
    dizer "o contexto foi truncado". Deriva aqui, e não em cada cliente, porque
    três clientes derivando a mesma coisa é como dois deles erram.
    """

    rules: int = 0
    findings: int = 0
    index: int = 0
    memories: int = 0
    truncated: bool = False


class FindingSummary(BaseModel):
    id: str
    thread_id: str = ""
    title: str = ""
    payload: dict = Field(default_factory=dict)


class ContextPackageSummary(BaseModel):
    demand_id: str
    rules: list[str] = Field(default_factory=list)
    index: list[ArtifactSummary] = Field(default_factory=list)
    memories: list[ArtifactSummary] = Field(default_factory=list)
    findings: list[FindingSummary] = Field(default_factory=list)
    estimated_tokens: int = 0
    # None = o núcleo não informou o descarte. NUNCA zeros de consolo.
    dropped: DroppedCounts | None = None


class MemoryHit(BaseModel):
    artifact: ArtifactSummary
    # None = o núcleo não pontuou este resultado (a busca lexical não pontua
    # como a semântica). Zero seria "nenhuma semelhança", que é outra coisa.
    score: float | None = None


class NewArtifact(BaseModel):
    """Escrita na base de conhecimento — o lado de volta do ciclo (ADR-0009 §4).

    O conteúdo chega em base64 porque no contrato do núcleo ele é `bytes`:
    artefato de conhecimento é markdown, JSON ou mapa gerado, em UTF-8 ou não, e
    fingir que é `str` transcodificaria em silêncio o que não for.
    """

    kind: str = Field(pattern="^(rule|index|memory)$")
    name: str = Field(min_length=1)
    # Vazio grava no escopo da CONTA — a regra que vale em todo projeto.
    project_id: str = ""
    content_base64: str = Field(min_length=1)
    meta: dict = Field(default_factory=dict)

    @field_validator("content_base64")
    @classmethod
    def _base64_valido(cls, v: str) -> str:
        """Recusa base64 inválido na BORDA, com 422.

        Sem isto o `b64decode` estouraria dentro do caso de uso e viraria 500 —
        erro de servidor para um pedido malformado do cliente.
        """
        try:
            base64.b64decode(v, validate=True)
        except (ValueError, TypeError) as exc:
            raise ValueError("conteúdo não é base64 válido") from exc
        return v

    def content(self) -> bytes:
        return base64.b64decode(self.content_base64, validate=True)


# ── tradução do núcleo para a borda ─────────────────────────────────────────


def _artifact(a: knowledge_pb2.KnowledgeArtifact) -> ArtifactSummary:
    # MessageToDict sobre o Struct devolve tipos Python puros em qualquer
    # profundidade; `dict(struct)` devolveria submensagens do protobuf, que o
    # Pydantic não sabe serializar.
    meta = MessageToDict(a.meta) if a.HasField("meta") else {}
    return ArtifactSummary(
        id=a.id,
        kind=_NOME_POR_KIND.get(a.kind, ""),
        project_id=a.project.id,
        name=a.name,
        version=a.version,
        object_ref=a.object_ref,
        # pop: a convenção interna do núcleo não atravessa para a tela.
        body=str(meta.pop(_META_BODY, "")),
        scope=str(meta.pop(_META_SCOPE, "")),
        meta=meta,
    )


def _finding(f) -> FindingSummary:
    return FindingSummary(
        id=f.id,
        thread_id=f.thread_id,
        title=f.title,
        payload=MessageToDict(f.payload) if f.HasField("payload") else {},
    )


def _descartes(pacote: knowledge_pb2.ContextPackage) -> DroppedCounts | None:
    """Contadores de descarte, lidos do campo `dropped` do núcleo.

    `dop.v1.ContextPackage.dropped` é um `map<string,int32>` por camada, e o
    núcleo o preenche SEMPRE — com as quatro chaves, inclusive zeradas. Um mapa
    VAZIO é, portanto, o núcleo que não informou (um núcleo anterior ao campo),
    e vira `None`: "não dá para saber" continua sendo diferente de zero, que é
    a AFIRMAÇÃO de que nada ficou de fora.

    Nota sobre o contorno que estava aqui antes: ele checava
    `DESCRIPTOR.fields_by_name` e depois `HasField("dropped")`, apostando que o
    campo chegaria como MENSAGEM. Chegou como mapa — e mapa não tem presença,
    então o `HasField` passou a levantar `ValueError` no primeiro pacote de
    contexto pedido. Contorno que se adianta ao formato do contrato não
    envelhece: quebra.

    `truncated` deriva de TODOS os valores do mapa, não só das quatro camadas
    conhecidas: se o núcleo passar a descartar numa camada nova, o número dela
    ainda não tem campo aqui, mas "o contexto foi truncado" continua verdade — e
    é essa a frase que a tela precisa dizer (ADR-0012).
    """
    d = pacote.dropped
    if not d:
        return None
    contagens = {camada: d.get(camada, 0) for camada in _CAMADAS}
    return DroppedCounts(**contagens, truncated=any(v > 0 for v in d.values()))


# ── casos de uso ────────────────────────────────────────────────────────────


@log
@account_scoped
async def get_context_package(demand_id: str) -> ContextPackageSummary:
    """A bagagem de bordo do agente para uma demanda, numa resposta só.

    A borda NÃO refaz a curadoria nem recalcula `estimated_tokens`: o que entra
    no pacote é decisão do núcleo (ADR-0009 §3), e um segundo critério de corte
    aqui divergiria do primeiro no primeiro ajuste de orçamento. O que a borda
    acrescenta é o que a tela precisa e o protobuf não dá de graça: o descarte
    como fato explícito, e não como silêncio.
    """
    ctx = auth_ctx.get()
    pacote = await stubs.knowledge_stub().BuildContextPackage(
        knowledge_pb2.BuildContextPackageRequest(
            ctx=call_context_from(ctx), demand_id=demand_id
        ),
        metadata=core.metadata(),
        timeout=_deadline(),
    )
    return ContextPackageSummary(
        demand_id=pacote.demand.id or demand_id,
        rules=list(pacote.rules),
        index=[_artifact(a) for a in pacote.index],
        memories=[_artifact(a) for a in pacote.memories],
        findings=[_finding(f) for f in pacote.findings],
        estimated_tokens=pacote.estimated_tokens,
        dropped=_descartes(pacote),
    )


@log
@account_scoped
async def search_memory(project_id: str, query: str, limit: int = 0) -> list[MemoryHit]:
    """O que não coube no pacote entra por aqui (ADR-0009 §3).

    O núcleo devolve artefatos e scores em listas PARALELAS; a borda os PAREIA.
    Não é enfeite: duas listas que o cliente precisa indexar em paralelo é um
    erro por distração esperando acontecer, e o erro seria silencioso — a
    memória errada com o score da vizinha ainda parece uma resposta plausível.
    """
    ctx = auth_ctx.get()
    pedido = knowledge_pb2.SearchMemoryRequest(
        ctx=call_context_from(ctx), query=query, limit=limit
    )
    if project_id:
        pedido.project.CopyFrom(common_pb2.ProjectRef(id=project_id))
    resp = await stubs.knowledge_stub().SearchMemory(
        pedido, metadata=core.metadata(), timeout=_deadline()
    )
    scores = list(resp.scores)
    return [
        MemoryHit(
            artifact=_artifact(a),
            # Score que o núcleo não mandou fica ausente. Preencher com 0.0
            # afirmaria "nenhuma semelhança" sobre um resultado que ele
            # devolveu justamente por ser semelhante.
            score=scores[i] if i < len(scores) else None,
        )
        for i, a in enumerate(resp.artifacts)
    ]


@log
@account_scoped
async def read_index(project_id: str, repo: str) -> ArtifactSummary:
    """O mapa de um repositório.

    Índice ausente vem como NOT_FOUND do núcleo e assim atravessa (404 no REST):
    o agente precisa SABER que não há mapa. Um índice vazio devolvido como se
    fosse índice mentiria com a mesma confiança de um índice desatualizado.
    """
    ctx = auth_ctx.get()
    a = await stubs.knowledge_stub().ReadIndex(
        knowledge_pb2.ReadIndexRequest(
            ctx=call_context_from(ctx),
            project=common_pb2.ProjectRef(id=project_id),
            repo=repo,
        ),
        metadata=core.metadata(),
        timeout=_deadline(),
    )
    return _artifact(a)


@log
@account_scoped
async def list_rules(project_id: str) -> list[str]:
    """As regras que VALEM para o projeto, com a herança já resolvida no núcleo."""
    ctx = auth_ctx.get()
    resp = await stubs.knowledge_stub().ListRules(
        knowledge_pb2.ListRulesRequest(
            ctx=call_context_from(ctx), project=common_pb2.ProjectRef(id=project_id)
        ),
        metadata=core.metadata(),
        timeout=_deadline(),
    )
    return list(resp.rules)


@log
@account_scoped
async def put_artifact(body: NewArtifact, idempotency_key: str = "") -> ArtifactSummary:
    """Grava conhecimento — a escrita de volta do ciclo (ADR-0009 §4).

    A chave de idempotência aqui não é redundante com uma UNIQUE do banco:
    regravar o mesmo nome no mesmo escopo é operação legítima (bumpa a versão),
    então sem chave um retry do canal viraria versão nova em silêncio.
    """
    ctx = auth_ctx.get()
    artefato = knowledge_pb2.KnowledgeArtifact(
        kind=_KIND_POR_NOME[body.kind],
        name=body.name,
    )
    if body.project_id:
        artefato.project.CopyFrom(common_pb2.ProjectRef(id=body.project_id))
    if body.meta:
        meta = struct_pb2.Struct()
        meta.update(body.meta)
        artefato.meta.CopyFrom(meta)
    a = await stubs.knowledge_stub().PutArtifact(
        knowledge_pb2.PutArtifactRequest(
            ctx=call_context_from(ctx),
            artifact=artefato,
            content=body.content(),
            idempotency_key=_idempotency(idempotency_key),
        ),
        metadata=core.metadata(),
        timeout=_deadline(),
    )
    return _artifact(a)
