"""The demand gRPC servicer — a protobuf adapter over the use cases.

Symmetric to `app/routers/demand.py`: it receives a message, calls the SAME
function from `app/usecases/demand.py`, returns a message. No decisions here —
neither authorization, nor a call to the core, nor the cockpit's derived fields
(those come ready from the use case, or the two ports would give different
answers to "where the demand is").

The stage vocabulary comes from `app/grpcapi/workflow.py`, as in the contract:
demand.proto imports workflow.proto's enums.
"""

from datetime import datetime

from google.protobuf import struct_pb2
from google.protobuf.json_format import MessageToDict

from app.grpcapi.gen.dop.bff.v1 import demand_pb2 as bff
from app.grpcapi.gen.dop.bff.v1 import demand_pb2_grpc as bff_grpc
from app.grpcapi.workflow import artifact_enum, gate_enum, stage_type_enum
from app.usecases import demand as uc

STATUS_BY_ENUM: dict[int, str] = {
    bff.DOP_STATUS_NEW: "new",
    bff.DOP_STATUS_DOING: "doing",
    bff.DOP_STATUS_DONE: "done",
    bff.DOP_STATUS_DELIVERED: "delivered",
}
ENUM_BY_STATUS = {name: value for value, name in STATUS_BY_ENUM.items()}

STAGE_BY_ENUM: dict[int, str] = {
    bff.STAGE_STATUS_PENDING: "pending",
    bff.STAGE_STATUS_RUNNING: "running",
    bff.STAGE_STATUS_BLOCKED: "blocked",
    bff.STAGE_STATUS_DONE: "done",
}
ENUM_BY_STAGE = {name: value for value, name in STAGE_BY_ENUM.items()}


def _instant(msg, field: str, value: datetime | None) -> None:
    """Fills the Timestamp in only when there is a date.

    Absent ≠ zeroed on the way back too: writing a zeroed Timestamp would say
    "this started in 1970", and on the other side HasField would answer that
    there is a date.
    """
    if value is not None:
        getattr(msg, field).FromDatetime(value)


def _artifact(a: uc.Artifact) -> bff.Artifact:
    return bff.Artifact(
        id=a.id,
        kind=artifact_enum(a.kind),
        name=a.name,
        object_ref=a.object_ref,
        version=a.version,
    )


def _stage(s: uc.Stage) -> bff.Stage:
    msg = bff.Stage(
        key=s.key,
        name=s.name,
        type=stage_type_enum(s.type),
        status=ENUM_BY_STAGE.get(s.status, bff.STAGE_STATUS_UNSPECIFIED),
        gate=gate_enum(s.gate),
        artifacts=[_artifact(a) for a in s.artifacts],
        awaiting_decision=s.awaiting_decision,
    )
    _instant(msg, "started_at", s.started_at)
    _instant(msg, "finished_at", s.finished_at)
    return msg


def _demand(d: uc.Demand) -> bff.Demand:
    return bff.Demand(
        id=d.id,
        project_id=d.project_id,
        external_key=d.external_key,
        title=d.title,
        card_type=d.card_type,
        provider_status=d.provider_status,
        dop_status=ENUM_BY_STATUS.get(d.dop_status, bff.DOP_STATUS_UNSPECIFIED),
        flow_id=d.flow_id,
        flow_version=d.flow_version,
        stages=[_stage(s) for s in d.stages],
        current_stage_key=d.current_stage_key,
        blocked=d.blocked,
        awaiting_decision=d.awaiting_decision,
    )


def _thread(t: uc.Thread) -> bff.Thread:
    msg = bff.Thread(id=t.id, key=t.key, blocked=t.blocked)
    # An absent brief and an empty brief are different things: a thread with no
    # agent behind it, and an agent with neither purpose nor budget.
    if t.card is not None:
        msg.card.CopyFrom(
            bff.AgentCard(
                purpose=t.card.purpose,
                tools=t.card.tools,
                model=t.card.model,
                effort=t.card.effort,
                budget_micros=t.card.budget_micros,
            )
        )
    return msg


def _message(m: uc.Message) -> bff.Message:
    msg = bff.Message(
        id=m.id,
        thread_id=m.thread_id,
        author_kind=m.author_kind,
        author_id=m.author_id,
        author_name=m.author_name,
        text=m.text,
    )
    _instant(msg, "at", m.at)
    return msg


def _finding(f: uc.Finding) -> bff.Finding:
    payload = struct_pb2.Struct()
    payload.update(f.payload)
    return bff.Finding(id=f.id, thread_id=f.thread_id, title=f.title, payload=payload)


class DemandServicer(bff_grpc.DemandServiceServicer):
    async def ListDemands(
        self, request: bff.ListDemandsRequest, context
    ) -> bff.ListDemandsResponse:
        pagina = await uc.list_demands(
            request.project_id, request.page_size, request.page_token
        )
        return bff.ListDemandsResponse(
            demands=[_demand(d) for d in pagina.demands],
            next_page_token=pagina.next_page_token,
        )

    async def GetDemand(self, request: bff.GetDemandRequest, context) -> bff.Demand:
        return _demand(await uc.get_demand(request.id))

    async def GetDemandCockpit(
        self, request: bff.GetDemandCockpitRequest, context
    ) -> bff.DemandCockpit:
        c = await uc.get_cockpit(request.demand_id)
        return bff.DemandCockpit(
            demand=_demand(c.demand),
            threads=[_thread(t) for t in c.threads],
            findings=[_finding(f) for f in c.findings],
        )

    async def StartDemand(self, request: bff.StartDemandRequest, context) -> bff.Demand:
        body = uc.NewDemand(
            project_id=request.project_id, external_key=request.external_key
        )
        return _demand(await uc.start_demand(body, request.idempotency_key))

    async def AdvanceStage(self, request: bff.AdvanceStageRequest, context) -> bff.Stage:
        body = uc.StageTransition(status=STAGE_BY_ENUM.get(request.status, ""))
        return _stage(
            await uc.advance_stage(
                request.demand_id, request.stage_key, body, request.idempotency_key
            )
        )

    async def DecideGate(self, request: bff.DecideGateRequest, context) -> bff.Stage:
        body = uc.GateDecision(approved=request.approved, comment=request.comment)
        return _stage(
            await uc.decide_gate(
                request.demand_id, request.stage_key, body, request.idempotency_key
            )
        )

    async def ListThreads(
        self, request: bff.ListThreadsRequest, context
    ) -> bff.ListThreadsResponse:
        threads = await uc.list_threads(request.demand_id)
        return bff.ListThreadsResponse(threads=[_thread(t) for t in threads])

    async def CreateThread(self, request: bff.CreateThreadRequest, context) -> bff.Thread:
        ficha = None
        # HasField on the way IN too: the client that sends no brief is not
        # asking for an empty brief.
        if request.HasField("card"):
            c = request.card
            ficha = uc.AgentCard(
                purpose=c.purpose,
                tools=list(c.tools),
                model=c.model,
                effort=c.effort,
                budget_micros=c.budget_micros,
            )
        body = uc.NewThread(key=request.key, card=ficha)
        return _thread(await uc.create_thread(request.demand_id, body, request.idempotency_key))

    async def PostMessage(self, request: bff.PostMessageRequest, context) -> bff.Message:
        body = uc.NewMessage(text=request.text)
        return _message(await uc.post_message(request.thread_id, body, request.idempotency_key))

    async def PublishFinding(
        self, request: bff.PublishFindingRequest, context
    ) -> bff.Finding:
        body = uc.NewFinding(
            thread_id=request.thread_id,
            title=request.title,
            payload=MessageToDict(request.payload) if request.HasField("payload") else {},
        )
        return _finding(
            await uc.publish_finding(request.demand_id, body, request.idempotency_key)
        )
