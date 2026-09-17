"""Knowledge use cases — rules, index and memory (ADR-0006).

The same discipline as `identity` and `hierarchy`: the rule lives here, the
router and the servicer translate. See `app/usecases/identity.py`'s docstring
for why the decorators live in the use case and not in the adapter.

**The rule that structures this module: what was DROPPED is first-class
information.** The context package is selected by a token budget (ADR-0008), and
what did not fit does not quietly vanish — the screen has to be able to say "the
context was truncated". Hence `ContextPackageSummary.dropped` being
`None`-able and never zeroed out of convenience: `None` is "the core did not
say", zero is the ASSERTION that nothing was left out. They are different
answers and the difference is the point.

The edge's other job here is undoing two shapes of the core that serve the
database and not the screen: `SearchMemory`'s parallel lists become pairs, and
the `meta`'s internal keys (`dop.body`, `dop.scope`) become fields.
"""

import base64

from google.protobuf import struct_pb2
from google.protobuf.json_format import MessageToDict
from pydantic import BaseModel, Field, field_validator

from app.coreclient import stubs
from app.coreclient.client import core
from app.coreclient.gen.dop.v1 import common_pb2, knowledge_pb2
from app.platform.logging.decorator import log
from app.platform.security.decorator import account_scoped
from app.settings import settings

# Name ↔ enum in one place only, as in `resource`. Spreading this table out is
# how the two ends start disagreeing about what a "memory" is.
_KIND_BY_NAME = {
    "rule": knowledge_pb2.KnowledgeArtifact.KIND_RULE,
    "index": knowledge_pb2.KnowledgeArtifact.KIND_INDEX,
    "memory": knowledge_pb2.KnowledgeArtifact.KIND_MEMORY,
}
_NAME_BY_KIND = {v: k for k, v in _KIND_BY_NAME.items()}

# OUR keys that the core hides inside the artifact's `meta`: the small
# artifact's body and the scope level. They are the core's internal convention —
# the edge promotes them to fields and REMOVES them from the meta, so the screen
# never has to know the "dop." prefix.
_META_BODY = "dop.body"
_META_SCOPE = "dop.scope"

# The context package's layers (ADR-0006 §1), in the order the token budget cuts
# them. They are the keys of the core's `map<string,int32> dropped` — written
# here once, so the translation neither depends on a map's iteration order nor
# repeats a literal in four places.
_LAYERS = ("rules", "findings", "index", "memories")


def _deadline() -> float:
    return settings.core_deadline_s


def _idempotency(key: str = "") -> str:
    return key or core.idempotency_key()


# ── the edge's models ───────────────────────────────────────────────────────


class ArtifactSummary(BaseModel):
    id: str
    kind: str
    project_id: str = ""
    name: str
    version: int = 1
    # Filled in only on a LARGE artifact, which the sandbox reads straight from
    # storage.
    object_ref: str = ""
    # The AUTHOR's meta, already without the core's internal keys.
    meta: dict = Field(default_factory=dict)
    # The small artifact's inline content.
    body: str = ""
    scope: str = ""


class DroppedCounts(BaseModel):
    """What was LEFT OUT of the package, per layer (ADR-0008).

    `truncated` is derived — it is the only field the screen has to consult to
    say "the context was truncated". It is derived here, and not in each client,
    because three clients deriving the same thing is how two of them get it
    wrong.
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
    # None = the core did not report the drops. NEVER consolation zeroes.
    dropped: DroppedCounts | None = None


class MemoryHit(BaseModel):
    artifact: ArtifactSummary
    # None = the core did not score this result (the lexical search does not
    # score like the semantic one). Zero would be "no similarity", which is
    # another thing.
    score: float | None = None


class NewArtifact(BaseModel):
    """A write into the knowledge base — the cycle's way back (ADR-0006 §4).

    The content arrives in base64 because in the core's contract it is `bytes`:
    a knowledge artifact is markdown, JSON or a generated map, in UTF-8 or not,
    and pretending it is a `str` would silently transcode whatever is not.
    """

    kind: str = Field(pattern="^(rule|index|memory)$")
    name: str = Field(min_length=1)
    # Empty writes into the ACCOUNT's scope — the rule that holds in every
    # project.
    project_id: str = ""
    content_base64: str = Field(min_length=1)
    meta: dict = Field(default_factory=dict)

    @field_validator("content_base64")
    @classmethod
    def _valid_base64(cls, v: str) -> str:
        """Refuses invalid base64 at the EDGE, with a 422.

        Without this the `b64decode` would blow up inside the use case and become
        a 500 — a server error for a malformed client request.
        """
        try:
            base64.b64decode(v, validate=True)
        except (ValueError, TypeError) as exc:
            raise ValueError("content is not valid base64") from exc
        return v

    def content(self) -> bytes:
        return base64.b64decode(self.content_base64, validate=True)


# ── translating the core into the edge ──────────────────────────────────────


def _artifact(a: knowledge_pb2.KnowledgeArtifact) -> ArtifactSummary:
    # MessageToDict over the Struct returns plain Python types at any depth;
    # `dict(struct)` would return protobuf submessages, which Pydantic does not
    # know how to serialize.
    meta = MessageToDict(a.meta) if a.HasField("meta") else {}
    return ArtifactSummary(
        id=a.id,
        kind=_NAME_BY_KIND.get(a.kind, ""),
        project_id=a.project.id,
        name=a.name,
        version=a.version,
        object_ref=a.object_ref,
        # pop: the core's internal convention does not cross to the screen.
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


def _drops(package: knowledge_pb2.ContextPackage) -> DroppedCounts | None:
    """The drop counters, read from the core's `dropped` field.

    `dop.v1.ContextPackage.dropped` is a `map<string,int32>` per layer, and the
    core fills it in ALWAYS — with all four keys, zeroed ones included. An EMPTY
    map is therefore the core not having reported (a core older than the field),
    and becomes `None`: "there is no way to know" stays different from zero,
    which is the ASSERTION that nothing was left out.

    A note on the workaround that used to be here: it checked
    `DESCRIPTOR.fields_by_name` and then `HasField("dropped")`, betting the field
    would arrive as a MESSAGE. It arrived as a map — and a map has no presence,
    so the `HasField` started raising `ValueError` on the first context package
    requested. A workaround that runs ahead of the contract's shape does not
    age: it breaks.

    `truncated` derives from ALL the map's values, not only the four known
    layers: if the core starts dropping in a new layer, its number has no field
    here yet, but "the context was truncated" is still true — and that is the
    sentence the screen needs to say (ADR-0008).
    """
    d = package.dropped
    if not d:
        return None
    counts = {layer: d.get(layer, 0) for layer in _LAYERS}
    return DroppedCounts(**counts, truncated=any(v > 0 for v in d.values()))


# ── use cases ───────────────────────────────────────────────────────────────


@log
@account_scoped
async def get_context_package(demand_id: str) -> ContextPackageSummary:
    """The agent's carry-on luggage for a demand, in a single response.

    The edge does NOT redo the curation nor recompute `estimated_tokens`: what
    goes into the package is the core's decision (ADR-0006 §3), and a second
    cutting criterion here would diverge from the first at the first budget
    adjustment. What the edge adds is what the screen needs and protobuf does not
    give for free: the drops as an explicit fact, and not as silence.
    """
    package = await stubs.knowledge_stub().BuildContextPackage(
        knowledge_pb2.BuildContextPackageRequest(demand_id=demand_id),
        metadata=core.metadata(),
        timeout=_deadline(),
    )
    return ContextPackageSummary(
        demand_id=package.demand.id or demand_id,
        rules=list(package.rules),
        index=[_artifact(a) for a in package.index],
        memories=[_artifact(a) for a in package.memories],
        findings=[_finding(f) for f in package.findings],
        estimated_tokens=package.estimated_tokens,
        dropped=_drops(package),
    )


@log
@account_scoped
async def search_memory(project_id: str, query: str, limit: int = 0) -> list[MemoryHit]:
    """What did not fit in the package comes in through here (ADR-0006 §3).

    The core returns artifacts and scores in PARALLEL lists; the edge PAIRS
    them. It is not decoration: two lists the client has to index in parallel is
    a mistake-by-distraction waiting to happen, and the mistake would be silent —
    the wrong memory with its neighbour's score still looks like a plausible
    answer.
    """
    request = knowledge_pb2.SearchMemoryRequest(query=query, limit=limit)
    if project_id:
        request.project.CopyFrom(common_pb2.ProjectRef(id=project_id))
    resp = await stubs.knowledge_stub().SearchMemory(
        request, metadata=core.metadata(), timeout=_deadline()
    )
    scores = list(resp.scores)
    return [
        MemoryHit(
            artifact=_artifact(a),
            # A score the core did not send stays absent. Filling it with 0.0
            # would assert "no similarity" about a result it returned precisely
            # for being similar.
            score=scores[i] if i < len(scores) else None,
        )
        for i, a in enumerate(resp.artifacts)
    ]


@log
@account_scoped
async def read_index(project_id: str, repo: str) -> ArtifactSummary:
    """A repository's map.

    A missing index comes back as NOT_FOUND from the core and crosses as such (a
    404 in REST): the agent has to KNOW there is no map. An empty index returned
    as if it were an index would lie with the same confidence as an out-of-date
    one.
    """
    a = await stubs.knowledge_stub().ReadIndex(
        knowledge_pb2.ReadIndexRequest(
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
    """The rules that HOLD for the project, with the inheritance already resolved in the core."""
    resp = await stubs.knowledge_stub().ListRules(
        knowledge_pb2.ListRulesRequest(project=common_pb2.ProjectRef(id=project_id)),
        metadata=core.metadata(),
        timeout=_deadline(),
    )
    return list(resp.rules)


@log
@account_scoped
async def put_artifact(body: NewArtifact, idempotency_key: str = "") -> ArtifactSummary:
    """Writes knowledge — the cycle's way back (ADR-0006 §4).

    The idempotency key here is not redundant with a database UNIQUE: rewriting
    the same name in the same scope is a legitimate operation (it bumps the
    version), so without the key a channel retry would silently become a new
    version.
    """
    artifact = knowledge_pb2.KnowledgeArtifact(
        kind=_KIND_BY_NAME[body.kind],
        name=body.name,
    )
    if body.project_id:
        artifact.project.CopyFrom(common_pb2.ProjectRef(id=body.project_id))
    if body.meta:
        meta = struct_pb2.Struct()
        meta.update(body.meta)
        artifact.meta.CopyFrom(meta)
    a = await stubs.knowledge_stub().PutArtifact(
        knowledge_pb2.PutArtifactRequest(
            artifact=artifact,
            content=body.content(),
            idempotency_key=_idempotency(idempotency_key),
        ),
        metadata=core.metadata(),
        timeout=_deadline(),
    )
    return _artifact(a)
