"""Fluxo de trabalho nos dois transportes, contra um núcleo fake.

Dois grupos de teste carregam o peso:

- **paridade** REST × gRPC, que impede alguém de reimplementar um caso de uso
  num adaptador sem ninguém notar na revisão;
- **procedência**, que é o que a borda entrega aqui: de onde veio cada etapa,
  lido dos CAMPOS `contributors`/`origins` do núcleo. Um dos testes existe
  justamente para provar que a borda NÃO depende mais do formato da frase
  `resolved_from` — ela é repassada inteira, e nada mais.

Os doubles e as fixtures vivem NESTE arquivo (conftest é território
compartilhado); o que já existe lá é importado.
"""

import grpc
import pytest
from fastapi.testclient import TestClient

from app.coreclient import stubs
from app.coreclient.gen.dop.v1 import workflow_pb2
from app.coreclient.resolver import CoreResolver
from app.grpcapi.gen.dop.bff.v1 import workflow_pb2 as bff
from app.grpcapi.gen.dop.bff.v1 import workflow_pb2_grpc as bff_grpc
from app.grpcapi.interceptors import AuthInterceptor, ErrorInterceptor, LoggingInterceptor
from app.grpcapi.workflow import WorkflowServicer
from app.main import create_app
from app.platform.security.firebase import FirebaseVerifier
from app.routers import workflow as rotas
from tests.conftest import PROJECT, FakeCall, metadata_for, token_for

ACCOUNT = metadata_for(token_for())
REST_HEADERS = {"authorization": token_for(), "x-account-id": "acct-1"}

# A frase exata que o núcleo escreve (renderTrail, em
# internal/domain/workflow/entity.go): cadeia do mais específico ao mais
# genérico, e o detalhe por etapa quando os níveis divergem.
RASTRO = "account ◂ plataforma — etapas: context (plataforma), spec (account)"


def _fluxo(**campos) -> workflow_pb2.Flow:
    f = workflow_pb2.Flow(
        id="flow-1",
        name="flow da account",
        description="composição da ACME",
        version=3,
        owner_scope="account",
        owner_id="acct-1",
        **campos,
    )
    f.stages.add(
        key="context",
        name="Contexto",
        type=workflow_pb2.STAGE_TYPE_CONTEXT,
        artifacts=[workflow_pb2.ARTIFACT_KIND_DOCUMENT],
        gate=workflow_pb2.GATE_NONE,
    )
    f.stages.add(
        key="spec",
        name="Spec",
        type=workflow_pb2.STAGE_TYPE_SPEC,
        artifacts=[workflow_pb2.ARTIFACT_KIND_SPEC],
        gate=workflow_pb2.GATE_HUMAN,
        subtypes=["aaa"],
    )
    return f


class FluxosFalsos:
    """Núcleo fake de flow. Registra o request e returns o que mandarem."""

    def __init__(self):
        self.flow = _fluxo()
        self.ListFlows = FakeCall(workflow_pb2.ListFlowsResponse(flows=[self.flow]))
        self.GetFlow = FakeCall(self.flow)
        self.CreateFlow = FakeCall(self.flow)
        self.UpdateFlow = FakeCall(self.flow)
        self.PromoteFlow = FakeCall(self.flow)
        self.ValidateFlow = FakeCall(
            workflow_pb2.ValidateFlowResponse(
                valid=False,
                errors=["etapa 'spec' repetida"],
                warnings=["flow sem etapa de teste"],
            )
        )
        self.ResolveFlow = FakeCall(self.efetivo())

    def efetivo(self, **campos) -> workflow_pb2.EffectiveFlow:
        """O flow efetivo como o núcleo o returns: campos E frase.

        Os dois juntos porque é assim que ele responde — e é a única forma de o
        teste conseguir provar que a borda lê os CAMPOS: se o double só mandasse
        a frase, ler dela passaria despercebido.
        """
        padrao = {
            "flow": self.flow,
            "resolved_from": RASTRO,
            "contributors": [
                workflow_pb2.ScopeRef(scope="account", id="acct-1"),
                workflow_pb2.ScopeRef(scope="platform"),
            ],
            "origins": [
                workflow_pb2.StageOrigin(key="context", scope="platform"),
                workflow_pb2.StageOrigin(key="spec", scope="account", scope_id="acct-1"),
            ],
        }
        return workflow_pb2.EffectiveFlow(**{**padrao, **campos})


@pytest.fixture
def flows(core, monkeypatch):
    fake = FluxosFalsos()
    monkeypatch.setattr(stubs, "workflow_stub", lambda: fake)
    return fake


def _app_com_rotas():
    """O app com as rotas de flow.

    `app/main.py` não é deste agente: enquanto o registro não chega lá, o teste
    monta o app e acrescenta o router. O `if` deixa o teste continuar válido
    depois do registro, sem rota duplicada.
    """
    app = create_app()
    if not any(getattr(r, "path", "") == "/api/v1/flows" for r in app.routes):
        app.include_router(rotas.router)
    return app


@pytest.fixture
def client_flow(flows):
    with TestClient(_app_com_rotas()) as c:
        yield c


@pytest.fixture
async def stub_flow(flows):
    """Servidor gRPC real, em porta efêmera, com os MESMOS interceptores da
    porta de produção. Próprio porque `app/grpcapi/server.py` ainda não
    registra este servicer, e esse arquivo não é deste agente."""
    server = grpc.aio.server(
        interceptors=(
            LoggingInterceptor(),
            ErrorInterceptor(),
            AuthInterceptor(FirebaseVerifier(PROJECT), CoreResolver()),
        )
    )
    bff_grpc.add_WorkflowServiceServicer_to_server(WorkflowServicer(), server)
    porta = server.add_insecure_port("127.0.0.1:0")
    await server.start()
    try:
        async with grpc.aio.insecure_channel(f"127.0.0.1:{porta}") as channel:
            yield bff_grpc.WorkflowServiceStub(channel)
    finally:
        await server.stop(0)


class TestProvenance:
    """De onde veio cada etapa — lido dos campos, não da frase."""

    def test_the_chain_and_the_origin_per_stage(self, client_flow):
        eff = client_flow.get(
            "/api/v1/flows/effective?scope=project&scope_id=prj-1",
            headers=REST_HEADERS,
        ).json()
        p = eff["provenance"]
        # Do mais específico ao mais genérico, no MESMO vocabulário de
        # owner_scope — que é o que o núcleo já manda no campo.
        assert p["contributors"] == ["account", "platform"]
        assert p["origins"] == [
            {"stage_key": "context", "scope": "platform"},
            {"stage_key": "spec", "scope": "account"},
        ]
        # As origens seguem a ordem das etapas: origins[i] é a origem de stages[i].
        assert [o["stage_key"] for o in p["origins"]] == [
            s["key"] for s in eff["flow"]["stages"]
        ]
        assert p["truncated"] is False

    def test_the_edge_does_not_depend_on_the_sentence_shape(self, client_flow, flows):
        """O teste que existe para provar que o parser MORREU.

        A frase vem irreconhecível — outra pontuação, outro idioma, sem os
        separadores e sem os parênteses que o parser antigo procurava. A
        procedência tem de sair idêntica mesmo assim, porque ela vem dos
        campos. Se alguém reintroduzir leitura da frase, é aqui que quebra.
        """
        flows.ResolveFlow.returns(
            flows.efetivo(resolved_from="resolved from account, then platform")
        )
        p = client_flow.get(
            "/api/v1/flows/effective?scope=project", headers=REST_HEADERS
        ).json()["provenance"]
        assert p["contributors"] == ["account", "platform"]
        assert [(o["stage_key"], o["scope"]) for o in p["origins"]] == [
            ("context", "platform"),
            ("spec", "account"),
        ]
        # E a frase, seja ela qual for, atravessa inteira e sem interpretação.
        assert p["sentence"] == "resolved from account, then platform"

    def test_the_original_sentence_is_not_lost(self, client_flow):
        """Quem só quer imprimir continua imprimindo — e quem desconfiar da
        leitura estruturada tem contra o que conferir."""
        eff = client_flow.get(
            "/api/v1/flows/effective?scope=project", headers=REST_HEADERS
        ).json()
        assert eff["provenance"]["sentence"] == RASTRO

    def test_a_single_level_also_has_an_origin_per_stage(self, client_flow, flows):
        """Quando só um nível declarou, a FRASE não traz o detalhe por etapa —
        não há divergência para explicar. Os campos trazem.

        É a diferença que a P-20 comprou: antes, a borda lia a frase e concluía
        "não há origens"; agora ela responde de onde veio cada etapa mesmo no
        caso em que o núcleo não achou que valia a pena escrever.
        """
        flows.ResolveFlow.returns(
            flows.efetivo(
                resolved_from="project",
                contributors=[workflow_pb2.ScopeRef(scope="project", id="prj-1")],
                origins=[
                    workflow_pb2.StageOrigin(key="context", scope="project", scope_id="prj-1"),
                    workflow_pb2.StageOrigin(key="spec", scope="project", scope_id="prj-1"),
                ],
            )
        )
        p = client_flow.get(
            "/api/v1/flows/effective?scope=project", headers=REST_HEADERS
        ).json()["provenance"]
        assert p["contributors"] == ["project"]
        assert [o["scope"] for o in p["origins"]] == ["project", "project"]
        assert p["truncated"] is False

    def test_a_stage_with_no_reported_origin_marks_truncated(self, client_flow, flows):
        """`truncated` é MEDIDO: origens que não cobrem as etapas.

        Antes ele era deduzido do "…" com que o núcleo corta a frase em 12
        etapas. O corte é da frase; a lista estruturada vem inteira. Medir
        mantém o campo correto se o núcleo passar a cortar a lista também — e
        o que ele promete ao client é o mesmo: há etapa cuja origem ninguém
        sabe informar, o que é diferente de ela não ter origem.
        """
        flows.ResolveFlow.returns(
            flows.efetivo(
                origins=[workflow_pb2.StageOrigin(key="context", scope="platform")]
            )
        )
        p = client_flow.get(
            "/api/v1/flows/effective?scope=project", headers=REST_HEADERS
        ).json()["provenance"]
        assert p["truncated"] is True
        assert [o["stage_key"] for o in p["origins"]] == ["context"]

    def test_with_no_provenance_no_provenance_is_invented(self, client_flow, flows):
        """Núcleo calado: nada se deduz, e `truncated` não vira alarme fake.

        Sem flow não há etapa para explicar — então não há origem faltando.
        """
        flows.ResolveFlow.returns(workflow_pb2.EffectiveFlow())
        p = client_flow.get(
            "/api/v1/flows/effective?scope=project", headers=REST_HEADERS
        ).json()["provenance"]
        assert p == {"contributors": [], "origins": [], "sentence": "", "truncated": False}


class TestREST:
    def test_the_flow_translates_the_stage_vocabulary(self, client_flow):
        f = client_flow.get("/api/v1/flows/flow-1", headers=REST_HEADERS).json()
        assert [s["type"] for s in f["stages"]] == ["context", "spec"]
        assert [s["gate"] for s in f["stages"]] == ["none", "human"]
        assert f["stages"][1]["artifacts"] == ["spec"]

    def test_validation_is_a_report_not_an_error(self, client_flow):
        """A screen precisa da lista para marcar as etapas; um 4xx só daria um toast."""
        r = client_flow.post(
            "/api/v1/flows/validate",
            headers=REST_HEADERS,
            json={"name": "f", "owner_scope": "project", "stages": []},
        )
        assert r.status_code == 200
        assert r.json()["valid"] is False
        assert r.json()["warnings"] == ["flow sem etapa de teste"]

    def test_effective_is_not_captured_as_a_flow_id(self, client_flow, flows):
        """A rota /flows/effective vem antes de /flows/{id} — se a ordem
        inverter, este teste cai."""
        client_flow.get("/api/v1/flows/effective?scope=account", headers=REST_HEADERS)
        assert flows.ResolveFlow.calls
        assert not flows.GetFlow.calls

    def test_creating_a_flow_carries_an_idempotency_key(self, client_flow, flows):
        client_flow.post(
            "/api/v1/flows",
            headers=REST_HEADERS,
            json={
                "name": "novo",
                "owner_scope": "project",
                "owner_id": "prj-1",
                "stages": [{"key": "spec", "type": "spec", "gate": "human"}],
            },
        )
        request = flows.CreateFlow.requests[0]
        assert request.idempotency_key != ""
        # E o vocabulário volta a ser enum na saída para o núcleo.
        assert request.flow.stages[0].type == workflow_pb2.STAGE_TYPE_SPEC
        assert request.flow.stages[0].gate == workflow_pb2.GATE_HUMAN

    def test_a_developer_composes_a_flow(self, client_flow, core):
        """Fluxo é conhecimento, aberto dentro da account (ADR-0014 §6)."""
        core.demote_to_developer()
        r = client_flow.post(
            "/api/v1/flows",
            headers=REST_HEADERS,
            json={"name": "novo", "owner_scope": "project", "owner_id": "prj-1"},
        )
        assert r.status_code == 201

    def test_a_developer_does_not_promote_a_flow(self, client_flow, core):
        """Promover muda o jeito de trabalhar de quem não pediu — exige gestão."""
        core.demote_to_developer()
        r = client_flow.post(
            "/api/v1/flows/flow-1/promotion",
            headers=REST_HEADERS,
            json={"target_scope": "account", "target_id": "acct-1"},
        )
        assert r.status_code == 403

    def test_with_no_active_account_it_is_refused(self, client_flow):
        r = client_flow.get(
            "/api/v1/flows", headers={"authorization": token_for()}
        )
        assert r.status_code == 400


class TestGRPC:
    async def test_resolve_returns_a_structured_provenance(self, stub_flow):
        eff = await stub_flow.ResolveFlow(
            bff.ResolveFlowRequest(scope="project", scope_id="prj-1"), metadata=ACCOUNT
        )
        assert list(eff.provenance.contributors) == ["account", "platform"]
        assert [(o.stage_key, o.scope) for o in eff.provenance.origins] == [
            ("context", "platform"),
            ("spec", "account"),
        ]
        assert eff.provenance.sentence == RASTRO

    async def test_an_absent_flow_is_not_an_empty_flow(self, stub_flow, flows):
        """Nenhum nível declarou flow: o campo não vem. Um flow zerado diria
        que existe um flow sem name e sem etapas."""
        flows.ResolveFlow.returns(workflow_pb2.EffectiveFlow(resolved_from=""))
        eff = await stub_flow.ResolveFlow(
            bff.ResolveFlowRequest(scope="account"), metadata=ACCOUNT
        )
        assert not eff.HasField("flow")

    async def test_a_developer_does_not_promote_a_flow(self, stub_flow, core):
        core.demote_to_developer()
        with pytest.raises(grpc.aio.AioRpcError) as e:
            await stub_flow.PromoteFlow(
                bff.PromoteFlowRequest(flow_id="flow-1", target_scope="account"),
                metadata=ACCOUNT,
            )
        assert e.value.code() == grpc.StatusCode.PERMISSION_DENIED

    async def test_with_no_token_it_is_unauthenticated(self, stub_flow):
        with pytest.raises(grpc.aio.AioRpcError) as e:
            await stub_flow.ListFlows(
                bff.ListFlowsRequest(), metadata=metadata_for(None)
            )
        assert e.value.code() == grpc.StatusCode.UNAUTHENTICATED

    async def test_a_core_error_crosses_with_its_own_status(self, stub_flow, flows):
        flows.GetFlow.fails_with(grpc.StatusCode.NOT_FOUND, "flow não encontrado")
        with pytest.raises(grpc.aio.AioRpcError) as e:
            await stub_flow.GetFlow(bff.GetFlowRequest(id="sumiu"), metadata=ACCOUNT)
        assert e.value.code() == grpc.StatusCode.NOT_FOUND


class TestParityBetweenTransports:
    """Uma função, dois adaptadores — e a prova de que continua assim."""

    async def test_the_effective_flow_is_the_same_on_both_ports(self, client_flow, stub_flow):
        rest = client_flow.get(
            "/api/v1/flows/effective?scope=project&scope_id=prj-1",
            headers=REST_HEADERS,
        ).json()
        grpc_resp = await stub_flow.ResolveFlow(
            bff.ResolveFlowRequest(scope="project", scope_id="prj-1"), metadata=ACCOUNT
        )

        assert grpc_resp.flow.id == rest["flow"]["id"]
        assert [s.key for s in grpc_resp.flow.stages] == [
            s["key"] for s in rest["flow"]["stages"]
        ]
        # A procedência é o que a borda constrói — se um adaptador a refizesse,
        # a diferença apareceria aqui.
        p_grpc, p_rest = grpc_resp.provenance, rest["provenance"]
        assert list(p_grpc.contributors) == p_rest["contributors"]
        assert [(o.stage_key, o.scope) for o in p_grpc.origins] == [
            (o["stage_key"], o["scope"]) for o in p_rest["origins"]
        ]
        assert p_grpc.sentence == p_rest["sentence"]
        assert p_grpc.truncated == p_rest["truncated"]
