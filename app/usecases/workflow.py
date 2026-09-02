"""Workflow use cases — the demand's cycle, and where it came from.

The same discipline as `identity` and `hierarchy`: the rule lives here and only
here; `app/routers/workflow.py` translates HTTP and `app/grpcapi/workflow.py`
translates protobuf, both calling these SAME functions. See
`app/usecases/identity.py`'s docstring for why the decorators live in the use
case — authorization pinned to the router would leave the gRPC door open.

What this module delivers to the client is the **provenance**: the effective
flow is the result of the chain `platform ◁ account ◁ workspace ◁ project ◁
demand` (ADR-0014 §3), and "why did this demand follow this flow?" is the
question that reaches support.

The core returns that in two ways, and the edge uses the right one.
`resolved_from` is a SENTENCE, good to print and terrible to use:

    "account ◂ platform — stages: context (platform), spec (account), …"

`contributors` and `origins` are the SAME facts, structured (P-19). The edge
reads the fields and passes the whole sentence on in `sentence`, for logs, error
messages and checking. Until P-20 it parsed the sentence, because the fields did
not exist — and a contract that forces the consumer to interpret text breaks the
day somebody improves the wording. The parser went out whole, along with the
label table it had to carry: the scope vocabulary already comes from the core
identical to `owner_scope`'s, with no translation in between.

This module also owns the STAGE vocabulary (type, artifact, gate), imported by
`demand`: in dop.v1 the demand's stage uses the enums declared in
workflow.proto, and repeating the tables on the other side would create two
truths about what a "spec" stage is.
"""

from pydantic import BaseModel, Field

from app.coreclient import stubs
from app.coreclient.client import core
from app.coreclient.gen.dop.v1 import workflow_pb2
from app.platform.logging.decorator import log
from app.platform.security.decorator import account_scoped, require_role
from app.settings import settings

# ── the stage vocabulary: proto number ↔ edge name ──────────────────────────
# One place only. `demand` imports from here instead of repeating, because the
# demand's stage is the instance of what the flow's specification describes.

_TYPE_BY_ENUM: dict[int, str] = {
    workflow_pb2.STAGE_TYPE_CONTEXT: "context",
    workflow_pb2.STAGE_TYPE_SPEC: "spec",
    workflow_pb2.STAGE_TYPE_PLAN: "plan",
    workflow_pb2.STAGE_TYPE_IMPLEMENTATION: "implementation",
    workflow_pb2.STAGE_TYPE_TEST: "test",
    workflow_pb2.STAGE_TYPE_HUMAN_VALIDATION: "human_validation",
    workflow_pb2.STAGE_TYPE_FINALIZATION: "finalization",
    workflow_pb2.STAGE_TYPE_GENERIC: "generic",
}
_ENUM_BY_TYPE = {name: value for value, name in _TYPE_BY_ENUM.items()}

_ARTIFACT_BY_ENUM: dict[int, str] = {
    workflow_pb2.ARTIFACT_KIND_DOCUMENT: "document",
    workflow_pb2.ARTIFACT_KIND_SPEC: "spec",
    workflow_pb2.ARTIFACT_KIND_PLAN: "plan",
    workflow_pb2.ARTIFACT_KIND_TEST_PLAN: "test_plan",
    workflow_pb2.ARTIFACT_KIND_DIAGRAM: "diagram",
    workflow_pb2.ARTIFACT_KIND_REPORT: "report",
}
_ENUM_BY_ARTIFACT = {name: value for value, name in _ARTIFACT_BY_ENUM.items()}

_GATE_BY_ENUM: dict[int, str] = {
    workflow_pb2.GATE_NONE: "none",
    workflow_pb2.GATE_HUMAN: "human",
}
_ENUM_BY_GATE = {name: value for value, name in _GATE_BY_ENUM.items()}


def stage_type_name(value: int) -> str:
    return _TYPE_BY_ENUM.get(value, "")


def stage_type_value(name: str) -> int:
    return _ENUM_BY_TYPE.get(name, workflow_pb2.STAGE_TYPE_UNSPECIFIED)


def artifact_name(value: int) -> str:
    return _ARTIFACT_BY_ENUM.get(value, "")


def artifact_value(name: str) -> int:
    return _ENUM_BY_ARTIFACT.get(name, workflow_pb2.ARTIFACT_KIND_UNSPECIFIED)


def gate_name(value: int) -> str:
    return _GATE_BY_ENUM.get(value, "")


def gate_value(name: str) -> int:
    return _ENUM_BY_GATE.get(name, workflow_pb2.GATE_UNSPECIFIED)


def _deadline() -> float:
    return settings.core_deadline_s


def _idempotency(key: str = "") -> str:
    return key or core.idempotency_key()


# ── the edge's models ───────────────────────────────────────────────────────


class StageSpec(BaseModel):
    key: str = Field(min_length=1)
    name: str = ""
    # The platform's CLOSED vocabulary (ADR-0014 §1): a new type requires the
    # platform to evolve. Composing stages, that is free.
    type: str = "generic"
    artifacts: list[str] = Field(default_factory=list)
    gate: str = "none"
    subtypes: list[str] = Field(default_factory=list)


class Flow(BaseModel):
    id: str = ""
    name: str = ""
    description: str = ""
    version: int = 0
    owner_scope: str = ""
    owner_id: str = ""
    stages: list[StageSpec] = Field(default_factory=list)


class NewFlow(BaseModel):
    """The flow goes in whole — v1 has no per-stage editing (ADR-0014 §2)."""

    name: str = Field(min_length=1)
    description: str = ""
    owner_scope: str = Field(min_length=1)
    owner_id: str = ""
    stages: list[StageSpec] = Field(default_factory=list)


class PromotionTarget(BaseModel):
    """The level the flow moves up to (demand → project → workspace → account)."""

    target_scope: str = Field(min_length=1)
    target_id: str = ""


class StageOrigin(BaseModel):
    stage_key: str
    scope: str


class Provenance(BaseModel):
    """The chain's trail, structured — see the module's docstring."""

    contributors: list[str] = Field(default_factory=list)
    origins: list[StageOrigin] = Field(default_factory=list)
    sentence: str = ""
    truncated: bool = False


class EffectiveFlow(BaseModel):
    # Absent when no level of the chain declared a flow. It is not an empty
    # flow: it is the absence of a flow, and the screen has to tell the two
    # apart.
    flow: Flow | None = None
    provenance: Provenance = Field(default_factory=Provenance)


class ValidationReport(BaseModel):
    """A report, not an error: the screen marks the problematic stages with the list."""

    valid: bool
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


# ── translating the core into the edge ──────────────────────────────────────


def _stage_spec(s: workflow_pb2.StageSpec) -> StageSpec:
    return StageSpec(
        key=s.key,
        name=s.name,
        type=stage_type_name(s.type),
        artifacts=[artifact_name(a) for a in s.artifacts],
        gate=gate_name(s.gate),
        subtypes=list(s.subtypes),
    )


def _flow(f: workflow_pb2.Flow) -> Flow:
    return Flow(
        id=f.id,
        name=f.name,
        description=f.description,
        version=f.version,
        owner_scope=f.owner_scope,
        owner_id=f.owner_id,
        stages=[_stage_spec(s) for s in f.stages],
    )


def _flow_for_the_core(body: NewFlow, flow_id: str = "") -> workflow_pb2.Flow:
    return workflow_pb2.Flow(
        id=flow_id,
        name=body.name,
        description=body.description,
        owner_scope=body.owner_scope,
        owner_id=body.owner_id,
        stages=[
            workflow_pb2.StageSpec(
                key=s.key,
                name=s.name,
                type=stage_type_value(s.type),
                artifacts=[artifact_value(a) for a in s.artifacts],
                gate=gate_value(s.gate),
                subtypes=s.subtypes,
            )
            for s in body.stages
        ],
    )


def _provenance(eff: workflow_pb2.EffectiveFlow) -> Provenance:
    """The provenance, read from the core's FIELDS — not from the sentence.

    `contributors` arrives as `ScopeRef{scope, id}` and `origins` as
    `StageOrigin{key, scope, scope_id}`, in the same order as `flow.stages`. The
    edge only swaps the names for its contract's; the scope vocabulary is
    already the same as `owner_scope`'s, so there is no translation table here —
    and it is precisely that table (with the sentence's labels) that went away
    when the parsing did.

    The contributor's `id` and the origin's `scope_id` are NOT passed on: the
    edge's contract still exposes them as a bare scope (`contributors` is a list
    of strings). Publishing them is a contract decision, not a debt — it is
    recorded in P-20's report as the next step, and the data is already here for
    when it is taken.

    `truncated` still means what the contract promises — "the origins do not
    cover every stage" — but it is now MEASURED, and not deduced from a "…" at
    the end of the sentence. The core's 12-stage cut is of the sentence, and of
    it alone; the structured list comes whole. Measuring instead of deducing is
    what keeps this field correct if the core one day starts cutting (or stops).
    """
    stage_count = len(eff.flow.stages) if eff.HasField("flow") else 0
    origins = [StageOrigin(stage_key=o.key, scope=o.scope) for o in eff.origins]
    return Provenance(
        contributors=[c.scope for c in eff.contributors],
        origins=origins,
        sentence=eff.resolved_from.strip(),
        truncated=len(origins) < stage_count,
    )


# ── use cases ───────────────────────────────────────────────────────────────


@log
@account_scoped
async def list_flows(owner_scope: str = "", owner_id: str = "") -> list[Flow]:
    """The flows visible in the active account; with a scope, only that level's."""
    resp = await stubs.workflow_stub().ListFlows(
        workflow_pb2.ListFlowsRequest(owner_scope=owner_scope, owner_id=owner_id),
        metadata=core.metadata(),
        timeout=_deadline(),
    )
    return [_flow(f) for f in resp.flows]


@log
@account_scoped
async def get_flow(flow_id: str) -> Flow:
    f = await stubs.workflow_stub().GetFlow(
        workflow_pb2.GetFlowRequest(id=flow_id),
        metadata=core.metadata(),
        timeout=_deadline(),
    )
    return _flow(f)


@log
@account_scoped
async def resolve_flow(scope: str, scope_id: str = "") -> EffectiveFlow:
    """A level's effective flow AND the trail of how it was reached.

    The resolution is the CORE's — it knows the whole chain and the overriding
    order. What the edge does is dress the provenance in the edge contract's
    vocabulary: see `_provenance` and the module's docstring.
    """
    eff = await stubs.workflow_stub().ResolveFlow(
        workflow_pb2.ResolveFlowRequest(scope=scope, scope_id=scope_id),
        metadata=core.metadata(),
        timeout=_deadline(),
    )
    # HasField: an absent flow and a zeroed flow are different things — no level
    # declared anything, versus a flow with no name and no stages.
    flow = _flow(eff.flow) if eff.HasField("flow") else None
    return EffectiveFlow(flow=flow, provenance=_provenance(eff))


@log
@account_scoped
@require_role("owner", "admin", "developer")
async def create_flow(body: NewFlow, idempotency_key: str = "") -> Flow:
    """A flow is KNOWLEDGE, not a credential: open within the account
    (ADR-0014 §6). That is why a developer composes their own project's flow —
    what requires management is PROMOTING, which changes the way of working of
    people who did not ask."""
    f = await stubs.workflow_stub().CreateFlow(
        workflow_pb2.CreateFlowRequest(
            flow=_flow_for_the_core(body),
            idempotency_key=_idempotency(idempotency_key),
        ),
        metadata=core.metadata(),
        timeout=_deadline(),
    )
    return _flow(f)


@log
@account_scoped
@require_role("owner", "admin", "developer")
async def update_flow(flow_id: str, body: NewFlow) -> Flow:
    """Changing it produces a NEW VERSION in the core.

    No idempotency_key on purpose: the one that versions is the core, and the
    call does not create a second aggregate if repeated — it produces another
    version of the same one, which is the requested effect. Demands under way
    carry on with the version they froze (ADR-0014 §4).
    """
    f = await stubs.workflow_stub().UpdateFlow(
        workflow_pb2.UpdateFlowRequest(flow=_flow_for_the_core(body, flow_id)),
        metadata=core.metadata(),
        timeout=_deadline(),
    )
    return _flow(f)


@log
@account_scoped
async def validate_flow(body: NewFlow) -> ValidationReport:
    """A dry run before writing — it changes nothing, so it requires no write role."""
    resp = await stubs.workflow_stub().ValidateFlow(
        workflow_pb2.ValidateFlowRequest(flow=_flow_for_the_core(body)),
        metadata=core.metadata(),
        timeout=_deadline(),
    )
    return ValidationReport(
        valid=resp.valid, errors=list(resp.errors), warnings=list(resp.warnings)
    )


@log
@account_scoped
@require_role("owner", "admin")
async def promote_flow(flow_id: str, body: PromotionTarget) -> Flow:
    """Promoting changes the process of people who did not ask — hence requiring management.

    ADR-0014 §5 asks for `manage` over the flow; until the core exposes a
    per-flow grant to the BFF, owner and admin (who have implicit manage over
    every resource) are the conservative approximation: one refusal too many,
    never one too few.
    """
    f = await stubs.workflow_stub().PromoteFlow(
        workflow_pb2.PromoteFlowRequest(
            flow_id=flow_id,
            target_scope=body.target_scope,
            target_id=body.target_id,
        ),
        metadata=core.metadata(),
        timeout=_deadline(),
    )
    return _flow(f)
