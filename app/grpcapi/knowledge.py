"""Servicer gRPC de conhecimento — adaptador protobuf sobre os casos de uso.

Simétrico a `app/routers/knowledge.py`: recebe mensagem, chama a MESMA função
de `app/usecases/knowledge.py`, devolve mensagem. Nenhuma decisão aqui — nem
autorização, nem curadoria, nem chamada ao núcleo.

O único cuidado que este arquivo exige é o de sempre: campo de mensagem só se
preenche quando existe. `dropped` ausente e `dropped` zerado dizem coisas
opostas sobre o pacote de contexto, e `CopyFrom` de um vazio apagaria a
diferença.
"""

import base64

from google.protobuf import struct_pb2
from google.protobuf.json_format import MessageToDict

from app.grpcapi.gen.dop.bff.v1 import demand_pb2 as bff_demand
from app.grpcapi.gen.dop.bff.v1 import knowledge_pb2 as bff
from app.grpcapi.gen.dop.bff.v1 import knowledge_pb2_grpc as bff_grpc
from app.usecases import knowledge as uc

# Enum da borda ↔ nome do caso de uso. Os números são os mesmos de
# dop.v1.KnowledgeArtifact.Kind, então a conversão é identidade, não tradução.
_NOME_POR_ENUM = {
    bff.KNOWLEDGE_KIND_RULE: "rule",
    bff.KNOWLEDGE_KIND_INDEX: "index",
    bff.KNOWLEDGE_KIND_MEMORY: "memory",
}
_ENUM_POR_NOME = {v: k for k, v in _NOME_POR_ENUM.items()}


def _struct(d: dict) -> struct_pb2.Struct:
    s = struct_pb2.Struct()
    s.update(d)
    return s


def _artifact(a: uc.ArtifactSummary) -> bff.KnowledgeArtifact:
    return bff.KnowledgeArtifact(
        id=a.id,
        kind=_ENUM_POR_NOME.get(a.kind, bff.KNOWLEDGE_KIND_UNSPECIFIED),
        project_id=a.project_id,
        name=a.name,
        version=a.version,
        object_ref=a.object_ref,
        meta=_struct(a.meta),
        body=a.body,
        scope=a.scope,
    )


def _finding(f: uc.FindingSummary) -> bff_demand.Finding:
    # `Finding` é do contrato de demanda: o achado do pacote de contexto e o do
    # cockpit são a MESMA coisa vista de dois lugares.
    return bff_demand.Finding(
        id=f.id, thread_id=f.thread_id, title=f.title, payload=_struct(f.payload)
    )


def _pacote(p: uc.ContextPackageSummary) -> bff.ContextPackage:
    msg = bff.ContextPackage(
        demand_id=p.demand_id,
        rules=p.rules,
        index=[_artifact(a) for a in p.index],
        memories=[_artifact(a) for a in p.memories],
        findings=[_finding(f) for f in p.findings],
        estimated_tokens=p.estimated_tokens,
    )
    # Só preenche quando o núcleo informou. Um `dropped` zerado afirmaria que
    # nada foi descartado — que é justamente a mentira que o campo existe para
    # impedir.
    if p.dropped is not None:
        msg.dropped.CopyFrom(
            bff.DroppedCounts(
                rules=p.dropped.rules,
                findings=p.dropped.findings,
                index=p.dropped.index,
                memories=p.dropped.memories,
                truncated=p.dropped.truncated,
            )
        )
    return msg


def _hit(h: uc.MemoryHit) -> bff.MemoryHit:
    msg = bff.MemoryHit(artifact=_artifact(h.artifact))
    # Score ausente fica ausente: `optional` no proto existe para isso.
    if h.score is not None:
        msg.score = h.score
    return msg


class KnowledgeServicer(bff_grpc.KnowledgeServiceServicer):
    async def GetContextPackage(
        self, request: bff.GetContextPackageRequest, context
    ) -> bff.ContextPackage:
        return _pacote(await uc.get_context_package(request.demand_id))

    async def SearchMemory(
        self, request: bff.SearchMemoryRequest, context
    ) -> bff.SearchMemoryResponse:
        hits = await uc.search_memory(request.project_id, request.query, request.limit)
        return bff.SearchMemoryResponse(hits=[_hit(h) for h in hits])

    async def ReadIndex(
        self, request: bff.ReadIndexRequest, context
    ) -> bff.KnowledgeArtifact:
        return _artifact(await uc.read_index(request.project_id, request.repo))

    async def ListRules(
        self, request: bff.ListRulesRequest, context
    ) -> bff.ListRulesResponse:
        return bff.ListRulesResponse(rules=await uc.list_rules(request.project_id))

    async def PutArtifact(
        self, request: bff.PutArtifactRequest, context
    ) -> bff.KnowledgeArtifact:
        # O caso de uso recebe base64 porque é assim que o REST o entrega; aqui
        # os bytes já vêm crus. Uma assinatura só para os dois transportes vale
        # o reencode — duas assinaturas valeriam duas implementações.
        body = uc.NewArtifact(
            kind=_NOME_POR_ENUM.get(request.kind, ""),
            name=request.name,
            project_id=request.project_id,
            content_base64=base64.b64encode(request.content).decode(),
            # MessageToDict e não `dict(...)`: o segundo devolveria
            # submensagens do protobuf nos níveis aninhados, e o Struct de
            # volta não as aceita.
            meta=MessageToDict(request.meta) if request.HasField("meta") else {},
        )
        return _artifact(await uc.put_artifact(body, request.idempotency_key))
