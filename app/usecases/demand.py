"""Demand use cases — the cockpit's unit of work.

The same discipline as `identity` and `hierarchy`: the rule lives here and only
here; `app/routers/demand.py` translates HTTP and `app/grpcapi/demand.py`
translates protobuf, both calling these SAME functions. The decorators live in
the use case, not in the adapter — see `app/usecases/identity.py`'s docstring.

Two things this module does that the core does not, and they are why the edge
exists:

1. **The cockpit in one call.** `get_cockpit` asks for the demand, the threads
   and the findings in PARALLEL and returns all three together. In series, the
   screen would assemble in three beats; asked for through separate RPCs by the
   client, the cockpit, dop-cli and the agent would each do their own
   orchestration.

2. **The derived fields of "where the demand is".** `current_stage_key`,
   `blocked` and `awaiting_decision` are the same little stage-reading rule each
   client would write its own way — and three different readings is how the
   demand list and the demand screen start disagreeing. `awaiting_decision`'s
   condition is the SAME one the core accepts in `DecideGate`: a human gate, a
   stage started and not finished.

The stage vocabulary (type, artifact, gate) comes from `usecases.workflow`, just
as in dop.v1 the demand imports workflow.proto's enums: the demand's stage is
the instance of what the flow's specification describes.
"""

import asyncio
from datetime import datetime

from google.protobuf import struct_pb2
from google.protobuf.json_format import MessageToDict
from pydantic import BaseModel, Field

from app.coreclient import stubs
from app.coreclient.client import core
from app.coreclient.gen.dop.v1 import common_pb2, demand_pb2, workflow_pb2
from app.platform.context import auth_ctx
from app.platform.logging.decorator import log
from app.platform.security.decorator import account_scoped, require_role
from app.settings import settings
from app.usecases.workflow import artifact_name, gate_name, stage_type_name

# The demand's situation in OUR vocabulary. The provider's travels raw, in
# `provider_status`: normalizing the two into the same field would erase the
# difference between what the platform knows and what the client's board says.
_STATUS_BY_ENUM: dict[int, str] = {
    demand_pb2.DOP_STATUS_NEW: "new",
    demand_pb2.DOP_STATUS_DOING: "doing",
    demand_pb2.DOP_STATUS_DONE: "done",
    demand_pb2.DOP_STATUS_DELIVERED: "delivered",
}

_STAGE_BY_ENUM: dict[int, str] = {
    demand_pb2.STAGE_STATUS_PENDING: "pending",
    demand_pb2.STAGE_STATUS_RUNNING: "running",
    demand_pb2.STAGE_STATUS_BLOCKED: "blocked",
    demand_pb2.STAGE_STATUS_DONE: "done",
}
_ENUM_BY_STAGE = {name: value for value, name in _STAGE_BY_ENUM.items()}

_ACTOR_BY_ENUM: dict[int, str] = {
    common_pb2.ActorRef.KIND_USER: "user",
    common_pb2.ActorRef.KIND_AGENT: "agent",
    common_pb2.ActorRef.KIND_SUBAGENT: "subagent",
    common_pb2.ActorRef.KIND_SYSTEM: "system",
}

# A stage waiting for a PERSON: a human gate, started and not finished. It is
# the condition the core requires in DecideGate — repeated here as a READ (the
# decision is still the core's), so the screen knows what to ask for before
# asking.
_OPEN_STAGES = (demand_pb2.STAGE_STATUS_RUNNING, demand_pb2.STAGE_STATUS_BLOCKED)


def stage_status_value(name: str) -> int:
    return _ENUM_BY_STAGE.get(name, demand_pb2.STAGE_STATUS_UNSPECIFIED)


def _deadline() -> float:
    return settings.core_deadline_s


def _idempotency(key: str = "") -> str:
    return key or core.idempotency_key()


def _instant(msg, field: str) -> datetime | None:
    """Protobuf's timestamp, or None when the field did not come.

    HasField, and not `!= 0`: a stage that has not started has no start, and a
    translated zero became 1970 on the screen — a wrong date is worse than no
    date.
    """
    if not msg.HasField(field):
        return None
    return getattr(msg, field).ToDatetime()


# ── the edge's models ───────────────────────────────────────────────────────


class Artifact(BaseModel):
    id: str
    kind: str = ""
    name: str = ""
    # An ObjectStore pointer. The content does NOT pass through here: the edge
    # delivers the reference and whoever needs the bytes fetches them with a
    # signed URL.
    object_ref: str = ""
    version: int = 0


class Stage(BaseModel):
    key: str
    name: str = ""
    type: str = ""
    status: str = ""
    gate: str = ""
    artifacts: list[Artifact] = Field(default_factory=list)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    awaiting_decision: bool = False


class Demand(BaseModel):
    id: str
    project_id: str = ""
    external_key: str = ""
    title: str = ""
    card_type: str = ""
    provider_status: str = ""
    dop_status: str = ""
    flow_id: str = ""
    flow_version: int = 0
    stages: list[Stage] = Field(default_factory=list)
    # Derived — see the module's docstring.
    current_stage_key: str = ""
    blocked: bool = False
    awaiting_decision: bool = False


class DemandPage(BaseModel):
    demands: list[Demand] = Field(default_factory=list)
    next_page_token: str = ""


class AgentCard(BaseModel):
    purpose: str = ""
    tools: list[str] = Field(default_factory=list)
    model: str = ""
    effort: str = ""
    budget_micros: int = 0


class Thread(BaseModel):
    id: str
    key: str = ""
    blocked: bool = False
    # Absent ≠ zeroed: a thread with no brief has no agent behind it; an empty
    # brief would be an agent with no purpose, no tools and no budget.
    card: AgentCard | None = None


class Message(BaseModel):
    id: str
    thread_id: str = ""
    author_kind: str = ""
    author_id: str = ""
    author_name: str = ""
    text: str = ""
    at: datetime | None = None


class Finding(BaseModel):
    id: str
    thread_id: str = ""
    title: str = ""
    payload: dict = Field(default_factory=dict)


class DemandCockpit(BaseModel):
    demand: Demand
    threads: list[Thread] = Field(default_factory=list)
    # An empty list here means "this demand has no findings", and only that —
    # see `get_cockpit` for why there is no longer a flag alongside.
    findings: list[Finding] = Field(default_factory=list)


class NewDemand(BaseModel):
    project_id: str = Field(min_length=1)
    # The demand's identity out there, on the provider's board (SUOPT-1315). The
    # flow is NOT chosen here: it is resolved by the chain and frozen.
    external_key: str = Field(min_length=1)


class StageTransition(BaseModel):
    """Where the stage goes. The vocabulary is closed and validated HERE, in the
    use case: that way the refusal of an invented status holds on both
    transports — a 422 in REST, INVALID_ARGUMENT in gRPC — with no repeated check
    in the adapter."""

    status: str = Field(pattern="^(pending|running|blocked|done)$")


class GateDecision(BaseModel):
    approved: bool
    comment: str = ""


class NewThread(BaseModel):
    key: str = Field(min_length=1)
    card: AgentCard | None = None


class NewMessage(BaseModel):
    text: str = Field(min_length=1)


class NewFinding(BaseModel):
    thread_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    payload: dict = Field(default_factory=dict)


# ── translating the core into the edge ──────────────────────────────────────


def _artifact(a: demand_pb2.Artifact) -> Artifact:
    return Artifact(
        id=a.id,
        kind=artifact_name(a.kind),
        name=a.name,
        object_ref=a.object_ref,
        version=a.version,
    )


def _stage(s: demand_pb2.DemandStage) -> Stage:
    return Stage(
        key=s.key,
        name=s.name,
        type=stage_type_name(s.type),
        status=_STAGE_BY_ENUM.get(s.status, ""),
        gate=gate_name(s.gate),
        artifacts=[_artifact(a) for a in s.artifacts],
        started_at=_instant(s, "started_at"),
        finished_at=_instant(s, "finished_at"),
        awaiting_decision=_awaits_decision(s),
    )


def _awaits_decision(s: demand_pb2.DemandStage) -> bool:
    return s.gate == workflow_pb2.GATE_HUMAN and s.status in _OPEN_STAGES


def _demand(d: demand_pb2.Demand) -> Demand:
    stages = [_stage(s) for s in d.stages]
    # The first one not finished is "where the demand is". Walking in the flow's
    # order matters: it is the order in which the core demands progress.
    current = next((e for e in stages if e.status != "done"), None)
    return Demand(
        id=d.id,
        project_id=d.project.id,
        external_key=d.external_key,
        title=d.title,
        card_type=d.card_type,
        provider_status=d.provider_status,
        dop_status=_STATUS_BY_ENUM.get(d.dop_status, ""),
        flow_id=d.flow_id,
        flow_version=d.flow_version,
        stages=stages,
        current_stage_key=current.key if current else "",
        blocked=any(e.status == "blocked" for e in stages),
        awaiting_decision=any(e.awaiting_decision for e in stages),
    )


def _thread(t: demand_pb2.Thread) -> Thread:
    card = None
    if t.HasField("card"):
        c = t.card
        card = AgentCard(
            purpose=c.purpose,
            tools=list(c.tools),
            model=c.model,
            effort=c.effort,
            budget_micros=c.budget_micros,
        )
    return Thread(id=t.id, key=t.key, blocked=t.blocked, card=card)


def _message(m: demand_pb2.Message) -> Message:
    return Message(
        id=m.id,
        thread_id=m.thread_id,
        author_kind=_ACTOR_BY_ENUM.get(m.author.kind, ""),
        author_id=m.author.id,
        author_name=m.author.name,
        text=m.text,
        at=_instant(m, "at"),
    )


def _finding(f: demand_pb2.Finding) -> Finding:
    # MessageToDict converts the WHOLE tree into Python types. Iterating the
    # Struct by hand would return nested protobuf submessages, which REST's JSON
    # serializer cannot write — and a finding is a free payload, so nested is the
    # normal case, not the exception.
    payload = MessageToDict(f.payload) if f.HasField("payload") else {}
    return Finding(id=f.id, thread_id=f.thread_id, title=f.title, payload=payload)


# ── use cases ───────────────────────────────────────────────────────────────


@log
@account_scoped
async def list_demands(
    project_id: str = "", page_size: int = 0, page_token: str = ""
) -> DemandPage:
    request = demand_pb2.ListDemandsRequest(
        page=common_pb2.PageRequest(size=page_size, token=page_token),
    )
    if project_id:
        request.project.id = project_id
    resp = await stubs.demand_stub().ListDemands(
        request, metadata=core.metadata(), timeout=_deadline()
    )
    return DemandPage(
        demands=[_demand(d) for d in resp.demands], next_page_token=resp.page.next_token
    )


@log
@account_scoped
async def get_demand(demand_id: str) -> Demand:
    d = await stubs.demand_stub().GetDemand(
        demand_pb2.GetDemandRequest(id=demand_id),
        metadata=core.metadata(),
        timeout=_deadline(),
    )
    return _demand(d)


@log
@account_scoped
async def list_threads(demand_id: str) -> list[Thread]:
    resp = await stubs.demand_stub().ListThreads(
        demand_pb2.ListThreadsRequest(demand_id=demand_id),
        metadata=core.metadata(),
        timeout=_deadline(),
    )
    return [_thread(t) for t in resp.threads]


@log
@account_scoped
async def list_findings(demand_id: str, thread_id: str = "") -> list[Finding]:
    """The demand's board of findings; with `thread_id`, only one thread's.

    It is the durable record of each concluded investigation (ADR-0009) — and it
    is what stops an agent, or a human, from redoing what another already
    finished.

    It reads through `DemandService.ListFindings`, and NOT through
    KnowledgeService's `BuildContextPackage`: that package is SELECTED by a token
    budget (it would show part of the findings as if they were all of them) and
    assembling it writes a measurement event — opening a screen would become a
    line of context cost.

    No pagination at the edge: the page is the core's. A board of findings is for
    READING, not for navigating; if a demand has more findings than the core's
    page holds, the problem to solve is not the screen's pagination.
    """
    resp = await stubs.demand_stub().ListFindings(
        demand_pb2.ListFindingsRequest(demand_id=demand_id, thread_id=thread_id),
        metadata=core.metadata(),
        timeout=_deadline(),
    )
    return [_finding(f) for f in resp.findings]


@log
@account_scoped
async def get_cockpit(demand_id: str) -> DemandCockpit:
    """The demand's whole screen: stages, threads and findings.

    In PARALLEL, not in series: the three calls do not depend on one another, and
    adding the latencies would turn the aggregation into a cost rather than a
    gain. `gather` propagates the first failure — a half response without saying
    it is half is worse than an error.

    **Why `findings_available` no longer exists.** The field was born when the
    core's contract only had `PublishFinding`: the list came back empty and the
    flag said "there is no way to know", because "this demand has no findings"
    and "the edge cannot read findings" are different facts and the screen needed
    to tell them apart. With `ListFindings` in the contract (P-19) the second
    fact stopped existing: either the read works, and the list is the answer, or
    it fails, and the `gather` makes the whole call fail with the core's status.
    There is no state left for the flag to describe — it would be a constant
    `true`, and a field that can only say one thing becomes noise somebody one
    day reads backwards.
    """
    demand, threads, findings = await asyncio.gather(
        get_demand(demand_id), list_threads(demand_id), list_findings(demand_id)
    )
    return DemandCockpit(demand=demand, threads=threads, findings=findings)


@log
@account_scoped
@require_role("owner", "admin", "developer")
async def start_demand(body: NewDemand, idempotency_key: str = "") -> Demand:
    """Starting RESOLVES and FREEZES the flow (ADR-0014 §4).

    That is why it is a write with an idempotency key and not a GET-or-create:
    the channel's retry with no key would open two demands for the same card.
    """
    d = await stubs.demand_stub().StartDemand(
        demand_pb2.StartDemandRequest(
            project=common_pb2.ProjectRef(id=body.project_id),
            external_key=body.external_key,
            idempotency_key=_idempotency(idempotency_key),
        ),
        metadata=core.metadata(),
        timeout=_deadline(),
    )
    return _demand(d)


@log
@account_scoped
@require_role("owner", "admin", "developer")
async def advance_stage(
    demand_id: str, stage_key: str, body: StageTransition, idempotency_key: str = ""
) -> Stage:
    """Moves the stage. The one that validates the transition is the core — the
    state machine is its own, and duplicating it here would create two rules for
    the same question."""
    s = await stubs.demand_stub().AdvanceStage(
        demand_pb2.AdvanceStageRequest(
            demand_id=demand_id,
            stage_key=stage_key,
            status=stage_status_value(body.status),
            idempotency_key=_idempotency(idempotency_key),
        ),
        metadata=core.metadata(),
        timeout=_deadline(),
    )
    return _stage(s)


@log
@account_scoped
@require_role("owner", "admin", "developer")
async def decide_gate(
    demand_id: str, stage_key: str, body: GateDecision, idempotency_key: str = ""
) -> Stage:
    """A human gate: approving finishes the stage, rejecting blocks it with the comment.

    A viewer does not decide — and the core also refuses any actor that is an
    agent, because a human gate decided by an agent is the gate not existing.
    """
    s = await stubs.demand_stub().DecideGate(
        demand_pb2.DecideGateRequest(
            demand_id=demand_id,
            stage_key=stage_key,
            approved=body.approved,
            comment=body.comment,
            idempotency_key=_idempotency(idempotency_key),
        ),
        metadata=core.metadata(),
        timeout=_deadline(),
    )
    return _stage(s)


@log
@account_scoped
@require_role("owner", "admin", "developer")
async def create_thread(demand_id: str, body: NewThread, idempotency_key: str = "") -> Thread:
    """Launches a thread (a subagent) on the demand — ADR-0010."""
    request = demand_pb2.CreateThreadRequest(
        demand_id=demand_id,
        key=body.key,
        idempotency_key=_idempotency(idempotency_key),
    )
    # It only fills the brief in when one came: sending a zeroed one would
    # declare an agent with no purpose, no tools and no budget.
    if body.card is not None:
        request.card.CopyFrom(
            demand_pb2.AgentCard(
                purpose=body.card.purpose,
                tools=body.card.tools,
                model=body.card.model,
                effort=body.card.effort,
                budget_micros=body.card.budget_micros,
            )
        )
    t = await stubs.demand_stub().CreateThread(
        request, metadata=core.metadata(), timeout=_deadline()
    )
    return _thread(t)


@log
@account_scoped
@require_role("owner", "admin", "developer")
async def post_message(
    thread_id: str,
    body: NewMessage,
    idempotency_key: str = "",
    actor_kind: str = "user",
) -> Message:
    """A message on the thread. Every message is an event (ADR-0006).

    `actor_kind` decides the AUTHORSHIP in the log. The default is human because
    the REST route and the servicer are only called by people; the runtime
    declares `agent` when it is the model's answer. Recording the agent's speech
    as the human's would make the log — which is the demand's truth — lie about
    who did what, on a platform whose entire premise is telling the two apart.
    """
    ctx = auth_ctx.get()
    m = await stubs.demand_stub().PostMessage(
        demand_pb2.PostMessageRequest(
            thread_id=thread_id,
            text=body.text,
            idempotency_key=_idempotency(idempotency_key),
        ),
        metadata=core.metadata_for(
            user_id=ctx.user_id,
            account_id=ctx.account_id,
            actor_name=ctx.principal.name or ctx.principal.email,
            actor_kind=actor_kind,
        ),
        timeout=_deadline(),
    )
    return _message(m)


@log
@account_scoped
@require_role("owner", "admin", "developer")
async def publish_finding(demand_id: str, body: NewFinding, idempotency_key: str = "") -> Finding:
    """Publishes a finding — the durable record of an investigation.

    Concluding a thread requires publishing the finding: the thread does not die
    in silence (the conversation spec §1), and it is the finding that goes into
    the siblings' context.
    """
    payload = struct_pb2.Struct()
    payload.update(body.payload)
    f = await stubs.demand_stub().PublishFinding(
        demand_pb2.PublishFindingRequest(
            demand_id=demand_id,
            thread_id=body.thread_id,
            title=body.title,
            payload=payload,
            idempotency_key=_idempotency(idempotency_key),
        ),
        metadata=core.metadata(),
        timeout=_deadline(),
    )
    return _finding(f)
