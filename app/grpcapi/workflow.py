"""The workflow gRPC servicer — a protobuf adapter over the use cases.

Symmetric to `app/routers/workflow.py`: it receives a message, calls the SAME
function from `app/usecases/workflow.py`, returns a message. No decisions here —
neither authorization nor a call to the core.

This module's enum tables are the STAGE vocabulary on the edge contract's side,
and `app/grpcapi/demand.py` imports from here instead of repeating: in
dop.bff.v1 the demand's stage uses the enums declared in workflow.proto, and two
tables for the same enum is how the ends start disagreeing about what a "spec"
stage is.
"""

from app.grpcapi.gen.dop.bff.v1 import workflow_pb2 as bff
from app.grpcapi.gen.dop.bff.v1 import workflow_pb2_grpc as bff_grpc
from app.usecases import workflow as uc

# The numbers are the same as dop.v1's, but the conversion is EXPLICIT: relying
# on the coincidence would work today and break silently the day one of the ends
# inserted a value in the middle.
TYPE_BY_ENUM: dict[int, str] = {
    bff.STAGE_TYPE_CONTEXT: "context",
    bff.STAGE_TYPE_SPEC: "spec",
    bff.STAGE_TYPE_PLAN: "plan",
    bff.STAGE_TYPE_IMPLEMENTATION: "implementation",
    bff.STAGE_TYPE_TEST: "test",
    bff.STAGE_TYPE_HUMAN_VALIDATION: "human_validation",
    bff.STAGE_TYPE_FINALIZATION: "finalization",
    bff.STAGE_TYPE_GENERIC: "generic",
}
ENUM_BY_TYPE = {name: value for value, name in TYPE_BY_ENUM.items()}

ARTIFACT_BY_ENUM: dict[int, str] = {
    bff.ARTIFACT_KIND_DOCUMENT: "document",
    bff.ARTIFACT_KIND_SPEC: "spec",
    bff.ARTIFACT_KIND_PLAN: "plan",
    bff.ARTIFACT_KIND_TEST_PLAN: "test_plan",
    bff.ARTIFACT_KIND_DIAGRAM: "diagram",
    bff.ARTIFACT_KIND_REPORT: "report",
}
ENUM_BY_ARTIFACT = {name: value for value, name in ARTIFACT_BY_ENUM.items()}

GATE_BY_ENUM: dict[int, str] = {
    bff.GATE_NONE: "none",
    bff.GATE_HUMAN: "human",
}
ENUM_BY_GATE = {name: value for value, name in GATE_BY_ENUM.items()}


def stage_type_enum(name: str) -> int:
    return ENUM_BY_TYPE.get(name, bff.STAGE_TYPE_UNSPECIFIED)


def artifact_enum(name: str) -> int:
    return ENUM_BY_ARTIFACT.get(name, bff.ARTIFACT_KIND_UNSPECIFIED)


def gate_enum(name: str) -> int:
    return ENUM_BY_GATE.get(name, bff.GATE_UNSPECIFIED)


def _stage_spec(s: uc.StageSpec) -> bff.StageSpec:
    return bff.StageSpec(
        key=s.key,
        name=s.name,
        type=stage_type_enum(s.type),
        artifacts=[artifact_enum(a) for a in s.artifacts],
        gate=gate_enum(s.gate),
        subtypes=s.subtypes,
    )


def _flow(f: uc.Flow) -> bff.Flow:
    return bff.Flow(
        id=f.id,
        name=f.name,
        description=f.description,
        version=f.version,
        owner_scope=f.owner_scope,
        owner_id=f.owner_id,
        stages=[_stage_spec(s) for s in f.stages],
    )


def _new_flow(f: bff.Flow) -> uc.NewFlow:
    return uc.NewFlow(
        name=f.name,
        description=f.description,
        owner_scope=f.owner_scope,
        owner_id=f.owner_id,
        stages=[
            uc.StageSpec(
                key=s.key,
                name=s.name,
                type=TYPE_BY_ENUM.get(s.type, "generic"),
                artifacts=[ARTIFACT_BY_ENUM.get(a, "") for a in s.artifacts],
                gate=GATE_BY_ENUM.get(s.gate, "none"),
                subtypes=list(s.subtypes),
            )
            for s in f.stages
        ],
    )


class WorkflowServicer(bff_grpc.WorkflowServiceServicer):
    async def ListFlows(self, request: bff.ListFlowsRequest, context) -> bff.ListFlowsResponse:
        fluxos = await uc.list_flows(request.owner_scope, request.owner_id)
        return bff.ListFlowsResponse(flows=[_flow(f) for f in fluxos])

    async def GetFlow(self, request: bff.GetFlowRequest, context) -> bff.Flow:
        return _flow(await uc.get_flow(request.id))

    async def CreateFlow(self, request: bff.CreateFlowRequest, context) -> bff.Flow:
        return _flow(await uc.create_flow(_new_flow(request.flow), request.idempotency_key))

    async def UpdateFlow(self, request: bff.UpdateFlowRequest, context) -> bff.Flow:
        return _flow(await uc.update_flow(request.id, _new_flow(request.flow)))

    async def ValidateFlow(
        self, request: bff.ValidateFlowRequest, context
    ) -> bff.ValidateFlowResponse:
        rel = await uc.validate_flow(_new_flow(request.flow))
        return bff.ValidateFlowResponse(
            valid=rel.valid, errors=rel.errors, warnings=rel.warnings
        )

    async def ResolveFlow(self, request: bff.ResolveFlowRequest, context) -> bff.EffectiveFlow:
        eff = await uc.resolve_flow(request.scope, request.scope_id)
        p = eff.provenance
        msg = bff.EffectiveFlow(
            provenance=bff.Provenance(
                contributors=p.contributors,
                origins=[
                    bff.StageOrigin(stage_key=o.stage_key, scope=o.scope) for o in p.origins
                ],
                sentence=p.sentence,
                truncated=p.truncated,
            )
        )
        # It only fills in when it exists: no level having declared a flow is
        # different from a flow with no name and no stages.
        if eff.flow is not None:
            msg.flow.CopyFrom(_flow(eff.flow))
        return msg

    async def PromoteFlow(self, request: bff.PromoteFlowRequest, context) -> bff.Flow:
        alvo = uc.PromotionTarget(
            target_scope=request.target_scope, target_id=request.target_id
        )
        return _flow(await uc.promote_flow(request.flow_id, alvo))
