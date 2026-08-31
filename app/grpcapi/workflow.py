"""Servicer gRPC de fluxo — adaptador protobuf sobre os casos de uso.

Simétrico a `app/routers/workflow.py`: recebe mensagem, chama a MESMA função de
`app/usecases/workflow.py`, devolve mensagem. Nenhuma decisão aqui — nem
autorização, nem chamada ao núcleo.

As tabelas de enum deste módulo são o vocabulário de ETAPA do lado do contrato
da borda, e `app/grpcapi/demand.py` importa daqui em vez de repetir: em
dop.bff.v1 a etapa da demanda usa os enums declarados em workflow.proto, e duas
tabelas para o mesmo enum é como as pontas passam a discordar sobre o que é uma
etapa de "spec".
"""

from app.grpcapi.gen.dop.bff.v1 import workflow_pb2 as bff
from app.grpcapi.gen.dop.bff.v1 import workflow_pb2_grpc as bff_grpc
from app.usecases import workflow as uc

# Os números são os mesmos de dop.v1, mas a conversão é EXPLÍCITA: depender da
# coincidência funcionaria hoje e quebraria em silêncio no dia em que uma das
# pontas inserisse um valor no meio.
TIPO_POR_ENUM: dict[int, str] = {
    bff.STAGE_TYPE_CONTEXT: "context",
    bff.STAGE_TYPE_SPEC: "spec",
    bff.STAGE_TYPE_PLAN: "plan",
    bff.STAGE_TYPE_IMPLEMENTATION: "implementation",
    bff.STAGE_TYPE_TEST: "test",
    bff.STAGE_TYPE_HUMAN_VALIDATION: "human_validation",
    bff.STAGE_TYPE_FINALIZATION: "finalization",
    bff.STAGE_TYPE_GENERIC: "generic",
}
ENUM_POR_TIPO = {nome: valor for valor, nome in TIPO_POR_ENUM.items()}

ARTEFATO_POR_ENUM: dict[int, str] = {
    bff.ARTIFACT_KIND_DOCUMENT: "document",
    bff.ARTIFACT_KIND_SPEC: "spec",
    bff.ARTIFACT_KIND_PLAN: "plan",
    bff.ARTIFACT_KIND_TEST_PLAN: "test_plan",
    bff.ARTIFACT_KIND_DIAGRAM: "diagram",
    bff.ARTIFACT_KIND_REPORT: "report",
}
ENUM_POR_ARTEFATO = {nome: valor for valor, nome in ARTEFATO_POR_ENUM.items()}

PORTAO_POR_ENUM: dict[int, str] = {
    bff.GATE_NONE: "none",
    bff.GATE_HUMAN: "human",
}
ENUM_POR_PORTAO = {nome: valor for valor, nome in PORTAO_POR_ENUM.items()}


def tipo_enum(nome: str) -> int:
    return ENUM_POR_TIPO.get(nome, bff.STAGE_TYPE_UNSPECIFIED)


def artefato_enum(nome: str) -> int:
    return ENUM_POR_ARTEFATO.get(nome, bff.ARTIFACT_KIND_UNSPECIFIED)


def portao_enum(nome: str) -> int:
    return ENUM_POR_PORTAO.get(nome, bff.GATE_UNSPECIFIED)


def _stage_spec(s: uc.StageSpec) -> bff.StageSpec:
    return bff.StageSpec(
        key=s.key,
        name=s.name,
        type=tipo_enum(s.type),
        artifacts=[artefato_enum(a) for a in s.artifacts],
        gate=portao_enum(s.gate),
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
                type=TIPO_POR_ENUM.get(s.type, "generic"),
                artifacts=[ARTEFATO_POR_ENUM.get(a, "") for a in s.artifacts],
                gate=PORTAO_POR_ENUM.get(s.gate, "none"),
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
        # Só preenche quando existe: nenhum nível declarou fluxo é diferente de
        # um fluxo sem nome e sem etapas.
        if eff.flow is not None:
            msg.flow.CopyFrom(_flow(eff.flow))
        return msg

    async def PromoteFlow(self, request: bff.PromoteFlowRequest, context) -> bff.Flow:
        alvo = uc.PromotionTarget(
            target_scope=request.target_scope, target_id=request.target_id
        )
        return _flow(await uc.promote_flow(request.flow_id, alvo))
