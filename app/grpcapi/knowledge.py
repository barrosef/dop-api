"""The knowledge gRPC servicer — a protobuf adapter over the use cases.

Symmetric to `app/routers/knowledge.py`: it receives a message, calls the SAME
function from `app/usecases/knowledge.py`, returns a message. No decisions here
— neither authorization, nor curation, nor a call to the core.

The only care this file requires is the usual one: a message field is only
filled in when it exists. An absent `dropped` and a zeroed `dropped` say
opposite things about the context package, and a `CopyFrom` of an empty one
would erase the difference.
"""

import base64

from google.protobuf import struct_pb2
from google.protobuf.json_format import MessageToDict

from app.grpcapi.gen.dop.bff.v1 import demand_pb2 as bff_demand
from app.grpcapi.gen.dop.bff.v1 import knowledge_pb2 as bff
from app.grpcapi.gen.dop.bff.v1 import knowledge_pb2_grpc as bff_grpc
from app.usecases import knowledge as uc

# The edge's enum ↔ the use case's name. The numbers are the same as
# dop.v1.KnowledgeArtifact.Kind's, so the conversion is the identity, not a
# translation.
_NAME_BY_ENUM = {
    bff.KNOWLEDGE_KIND_RULE: "rule",
    bff.KNOWLEDGE_KIND_INDEX: "index",
    bff.KNOWLEDGE_KIND_MEMORY: "memory",
}
_ENUM_BY_NAME = {v: k for k, v in _NAME_BY_ENUM.items()}


def _struct(d: dict) -> struct_pb2.Struct:
    s = struct_pb2.Struct()
    s.update(d)
    return s


def _artifact(a: uc.ArtifactSummary) -> bff.KnowledgeArtifact:
    return bff.KnowledgeArtifact(
        id=a.id,
        kind=_ENUM_BY_NAME.get(a.kind, bff.KNOWLEDGE_KIND_UNSPECIFIED),
        project_id=a.project_id,
        name=a.name,
        version=a.version,
        object_ref=a.object_ref,
        meta=_struct(a.meta),
        body=a.body,
        scope=a.scope,
    )


def _finding(f: uc.FindingSummary) -> bff_demand.Finding:
    # `Finding` belongs to the demand's contract: the context package's finding
    # and the cockpit's are the SAME thing seen from two places.
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
    # It only fills in when the core reported. A zeroed `dropped` would assert
    # that nothing was dropped — which is precisely the lie the field exists to
    # prevent.
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
    # An absent score stays absent: `optional` in the proto exists for that.
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
        # The use case takes base64 because that is how REST delivers it; here
        # the bytes already arrive raw. A single signature for both transports is
        # worth the re-encode — two signatures would be worth two
        # implementations.
        body = uc.NewArtifact(
            kind=_NAME_BY_ENUM.get(request.kind, ""),
            name=request.name,
            project_id=request.project_id,
            content_base64=base64.b64encode(request.content).decode(),
            # MessageToDict and not `dict(...)`: the second would return
            # protobuf submessages at the nested levels, and the Struct on the
            # way back does not accept them.
            meta=MessageToDict(request.meta) if request.HasField("meta") else {},
        )
        return _artifact(await uc.put_artifact(body, request.idempotency_key))
