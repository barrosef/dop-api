"""Servicer gRPC de demanda — adaptador protobuf sobre os casos de uso.

Simétrico a `app/routers/demand.py`: recebe mensagem, chama a MESMA função de
`app/usecases/demand.py`, devolve mensagem. Nenhuma decisão aqui — nem
autorização, nem chamada ao núcleo, nem os derivados do cockpit (esses vêm
prontos do caso de uso, senão as duas portas dariam respostas diferentes para
"onde a demanda está").

O vocabulário de etapa vem de `app/grpcapi/workflow.py`, como no contrato:
demand.proto importa os enums de workflow.proto.
"""

from datetime import datetime

from google.protobuf import struct_pb2
from google.protobuf.json_format import MessageToDict

from app.grpcapi.gen.dop.bff.v1 import demand_pb2 as bff
from app.grpcapi.gen.dop.bff.v1 import demand_pb2_grpc as bff_grpc
from app.grpcapi.workflow import artefato_enum, portao_enum, tipo_enum
from app.usecases import demand as uc

STATUS_POR_ENUM: dict[int, str] = {
    bff.DOP_STATUS_NEW: "new",
    bff.DOP_STATUS_DOING: "doing",
    bff.DOP_STATUS_DONE: "done",
    bff.DOP_STATUS_DELIVERED: "delivered",
}
ENUM_POR_STATUS = {nome: valor for valor, nome in STATUS_POR_ENUM.items()}

ETAPA_POR_ENUM: dict[int, str] = {
    bff.STAGE_STATUS_PENDING: "pending",
    bff.STAGE_STATUS_RUNNING: "running",
    bff.STAGE_STATUS_BLOCKED: "blocked",
    bff.STAGE_STATUS_DONE: "done",
}
ENUM_POR_ETAPA = {nome: valor for valor, nome in ETAPA_POR_ENUM.items()}


def _instante(msg, campo: str, valor: datetime | None) -> None:
    """Preenche o Timestamp só quando há data.

    Ausente ≠ zerado também na volta: escrever um Timestamp zerado diria "isto
    começou em 1970", e do outro lado o HasField responderia que há data.
    """
    if valor is not None:
        getattr(msg, campo).FromDatetime(valor)


def _artifact(a: uc.Artifact) -> bff.Artifact:
    return bff.Artifact(
        id=a.id,
        kind=artefato_enum(a.kind),
        name=a.name,
        object_ref=a.object_ref,
        version=a.version,
    )


def _stage(s: uc.Stage) -> bff.Stage:
    msg = bff.Stage(
        key=s.key,
        name=s.name,
        type=tipo_enum(s.type),
        status=ENUM_POR_ETAPA.get(s.status, bff.STAGE_STATUS_UNSPECIFIED),
        gate=portao_enum(s.gate),
        artifacts=[_artifact(a) for a in s.artifacts],
        awaiting_decision=s.awaiting_decision,
    )
    _instante(msg, "started_at", s.started_at)
    _instante(msg, "finished_at", s.finished_at)
    return msg


def _demand(d: uc.Demand) -> bff.Demand:
    return bff.Demand(
        id=d.id,
        project_id=d.project_id,
        external_key=d.external_key,
        title=d.title,
        card_type=d.card_type,
        provider_status=d.provider_status,
        dop_status=ENUM_POR_STATUS.get(d.dop_status, bff.DOP_STATUS_UNSPECIFIED),
        flow_id=d.flow_id,
        flow_version=d.flow_version,
        stages=[_stage(s) for s in d.stages],
        current_stage_key=d.current_stage_key,
        blocked=d.blocked,
        awaiting_decision=d.awaiting_decision,
    )


def _thread(t: uc.Thread) -> bff.Thread:
    msg = bff.Thread(id=t.id, key=t.key, blocked=t.blocked)
    # Ficha ausente e ficha vazia são coisas diferentes: uma thread sem agente
    # atrás, e um agente sem propósito nem orçamento.
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
    _instante(msg, "at", m.at)
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
        body = uc.StageTransition(status=ETAPA_POR_ENUM.get(request.status, ""))
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
        # HasField na ENTRADA também: o cliente que não manda ficha não está
        # pedindo uma ficha vazia.
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
