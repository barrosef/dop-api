"""Fluxo de trabalho nos dois transportes, contra um núcleo falso.

Dois grupos de teste carregam o peso:

- **paridade** REST × gRPC, que impede alguém de reimplementar um caso de uso
  num adaptador sem ninguém notar na revisão;
- **procedência**, que é o que a borda ACRESCENTA aqui: o núcleo achata o
  rastro da cadeia numa frase, e desmontá-la é a diferença entre a tela poder
  dizer "esta etapa veio da conta" e o cliente ter de fazer parsing sozinho.

Os duplos e as fixtures vivem NESTE arquivo (conftest é território
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
from tests.conftest import PROJECT, ChamadaFalsa, metadados_de, token_de

CONTA = metadados_de(token_de())
CABECALHOS_REST = {"authorization": token_de(), "x-account-id": "acct-1"}

# A frase exata que o núcleo escreve (renderTrail, em
# internal/domain/workflow/entity.go): cadeia do mais específico ao mais
# genérico, e o detalhe por etapa quando os níveis divergem.
RASTRO = "conta ◂ plataforma — etapas: contexto (plataforma), spec (conta)"


def _fluxo(**campos) -> workflow_pb2.Flow:
    f = workflow_pb2.Flow(
        id="flow-1",
        name="fluxo da conta",
        description="composição da ACME",
        version=3,
        owner_scope="account",
        owner_id="acct-1",
        **campos,
    )
    f.stages.add(
        key="contexto",
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
    """Núcleo falso de fluxo. Registra o pedido e devolve o que mandarem."""

    def __init__(self):
        self.fluxo = _fluxo()
        self.ListFlows = ChamadaFalsa(workflow_pb2.ListFlowsResponse(flows=[self.fluxo]))
        self.GetFlow = ChamadaFalsa(self.fluxo)
        self.CreateFlow = ChamadaFalsa(self.fluxo)
        self.UpdateFlow = ChamadaFalsa(self.fluxo)
        self.PromoteFlow = ChamadaFalsa(self.fluxo)
        self.ValidateFlow = ChamadaFalsa(
            workflow_pb2.ValidateFlowResponse(
                valid=False,
                errors=["etapa 'spec' repetida"],
                warnings=["fluxo sem etapa de teste"],
            )
        )
        self.ResolveFlow = ChamadaFalsa(
            workflow_pb2.EffectiveFlow(flow=self.fluxo, resolved_from=RASTRO)
        )


@pytest.fixture
def fluxos(nucleo, monkeypatch):
    falso = FluxosFalsos()
    monkeypatch.setattr(stubs, "workflow_stub", lambda: falso)
    return falso


def _app_com_rotas():
    """O app com as rotas de fluxo.

    `app/main.py` não é deste agente: enquanto o registro não chega lá, o teste
    monta o app e acrescenta o router. O `if` deixa o teste continuar válido
    depois do registro, sem rota duplicada.
    """
    app = create_app()
    if not any(getattr(r, "path", "") == "/api/v1/flows" for r in app.routes):
        app.include_router(rotas.router)
    return app


@pytest.fixture
def cliente_flow(fluxos):
    with TestClient(_app_com_rotas()) as c:
        yield c


@pytest.fixture
async def stub_flow(fluxos):
    """Servidor gRPC real, em porta efêmera, com os MESMOS interceptores da
    porta de produção. Próprio porque `app/grpcapi/server.py` ainda não
    registra este servicer, e esse arquivo não é deste agente."""
    servidor = grpc.aio.server(
        interceptors=(
            LoggingInterceptor(),
            ErrorInterceptor(),
            AuthInterceptor(FirebaseVerifier(PROJECT), CoreResolver()),
        )
    )
    bff_grpc.add_WorkflowServiceServicer_to_server(WorkflowServicer(), servidor)
    porta = servidor.add_insecure_port("127.0.0.1:0")
    await servidor.start()
    try:
        async with grpc.aio.insecure_channel(f"127.0.0.1:{porta}") as canal:
            yield bff_grpc.WorkflowServiceStub(canal)
    finally:
        await servidor.stop(0)


class TestProcedencia:
    """O rastro deixa de ser frase e vira dado — sem deixar de ser frase."""

    def test_cadeia_e_origem_por_etapa(self, cliente_flow):
        eff = cliente_flow.get(
            "/api/v1/flows/effective?scope=project&scope_id=prj-1",
            headers=CABECALHOS_REST,
        ).json()
        p = eff["provenance"]
        # Do mais específico ao mais genérico, no MESMO vocabulário de
        # owner_scope — e não nos rótulos em português da frase.
        assert p["contributors"] == ["account", "platform"]
        assert p["origins"] == [
            {"stage_key": "contexto", "scope": "platform"},
            {"stage_key": "spec", "scope": "account"},
        ]
        # As origens seguem a ordem das etapas: origins[i] é a origem de stages[i].
        assert [o["stage_key"] for o in p["origins"]] == [
            s["key"] for s in eff["flow"]["stages"]
        ]

    def test_a_frase_original_nao_se_perde(self, cliente_flow):
        """Quem só quer imprimir continua imprimindo — e quem desconfiar da
        leitura estruturada tem contra o que conferir."""
        eff = cliente_flow.get(
            "/api/v1/flows/effective?scope=project", headers=CABECALHOS_REST
        ).json()
        assert eff["provenance"]["sentence"] == RASTRO
        assert eff["provenance"]["truncated"] is False

    def test_um_nivel_so_nao_tem_detalhe_por_etapa(self, cliente_flow, fluxos):
        """Quando só um nível declarou, o núcleo não escreve o detalhe: não há
        divergência para explicar."""
        fluxos.ResolveFlow.devolve(
            workflow_pb2.EffectiveFlow(flow=fluxos.fluxo, resolved_from="projeto")
        )
        p = cliente_flow.get(
            "/api/v1/flows/effective?scope=project", headers=CABECALHOS_REST
        ).json()["provenance"]
        assert p["contributors"] == ["project"]
        assert p["origins"] == []

    def test_corte_do_nucleo_e_declarado(self, cliente_flow, fluxos):
        """O núcleo corta o detalhe em 12 etapas.

        Quando corta, dizer que cortou é o ponto: sem isso o cliente concluiria
        que as etapas restantes não têm origem.
        """
        fluxos.ResolveFlow.devolve(
            workflow_pb2.EffectiveFlow(
                flow=fluxos.fluxo,
                resolved_from="conta ◂ plataforma — etapas: contexto (plataforma), …",
            )
        )
        p = cliente_flow.get(
            "/api/v1/flows/effective?scope=project", headers=CABECALHOS_REST
        ).json()["provenance"]
        assert p["truncated"] is True
        assert [o["stage_key"] for o in p["origins"]] == ["contexto"]

    def test_pedaco_fora_do_formato_e_ignorado_nao_adivinhado(self, cliente_flow, fluxos):
        """Origem inventada é pior que origem nenhuma: a pergunta que estamos
        respondendo é justamente 'de onde veio isto?'."""
        fluxos.ResolveFlow.devolve(
            workflow_pb2.EffectiveFlow(
                flow=fluxos.fluxo,
                resolved_from=(
                    "conta ◂ plataforma — etapas: contexto (plataforma), lixo sem parenteses"
                ),
            )
        )
        p = cliente_flow.get(
            "/api/v1/flows/effective?scope=project", headers=CABECALHOS_REST
        ).json()["provenance"]
        assert [o["stage_key"] for o in p["origins"]] == ["contexto"]
        # E nada se perde: a frase inteira continua lá.
        assert "lixo sem parenteses" in p["sentence"]

    def test_rastro_vazio_nao_inventa_procedencia(self, cliente_flow, fluxos):
        fluxos.ResolveFlow.devolve(
            workflow_pb2.EffectiveFlow(flow=fluxos.fluxo, resolved_from="")
        )
        p = cliente_flow.get(
            "/api/v1/flows/effective?scope=project", headers=CABECALHOS_REST
        ).json()["provenance"]
        assert p == {"contributors": [], "origins": [], "sentence": "", "truncated": False}


class TestREST:
    def test_fluxo_traduz_o_vocabulario_de_etapa(self, cliente_flow):
        f = cliente_flow.get("/api/v1/flows/flow-1", headers=CABECALHOS_REST).json()
        assert [s["type"] for s in f["stages"]] == ["context", "spec"]
        assert [s["gate"] for s in f["stages"]] == ["none", "human"]
        assert f["stages"][1]["artifacts"] == ["spec"]

    def test_validacao_e_relatorio_nao_erro(self, cliente_flow):
        """A tela precisa da lista para marcar as etapas; um 4xx só daria um toast."""
        r = cliente_flow.post(
            "/api/v1/flows/validate",
            headers=CABECALHOS_REST,
            json={"name": "f", "owner_scope": "project", "stages": []},
        )
        assert r.status_code == 200
        assert r.json()["valid"] is False
        assert r.json()["warnings"] == ["fluxo sem etapa de teste"]

    def test_efetivo_nao_e_capturado_como_id_de_fluxo(self, cliente_flow, fluxos):
        """A rota /flows/effective vem antes de /flows/{id} — se a ordem
        inverter, este teste cai."""
        cliente_flow.get("/api/v1/flows/effective?scope=account", headers=CABECALHOS_REST)
        assert fluxos.ResolveFlow.chamadas
        assert not fluxos.GetFlow.chamadas

    def test_criar_fluxo_carrega_idempotencia(self, cliente_flow, fluxos):
        cliente_flow.post(
            "/api/v1/flows",
            headers=CABECALHOS_REST,
            json={
                "name": "novo",
                "owner_scope": "project",
                "owner_id": "prj-1",
                "stages": [{"key": "spec", "type": "spec", "gate": "human"}],
            },
        )
        pedido = fluxos.CreateFlow.pedidos[0]
        assert pedido.idempotency_key != ""
        # E o vocabulário volta a ser enum na saída para o núcleo.
        assert pedido.flow.stages[0].type == workflow_pb2.STAGE_TYPE_SPEC
        assert pedido.flow.stages[0].gate == workflow_pb2.GATE_HUMAN

    def test_developer_compoe_fluxo(self, cliente_flow, nucleo):
        """Fluxo é conhecimento, aberto dentro da conta (ADR-0014 §6)."""
        nucleo.papel_developer()
        r = cliente_flow.post(
            "/api/v1/flows",
            headers=CABECALHOS_REST,
            json={"name": "novo", "owner_scope": "project", "owner_id": "prj-1"},
        )
        assert r.status_code == 201

    def test_developer_nao_promove_fluxo(self, cliente_flow, nucleo):
        """Promover muda o jeito de trabalhar de quem não pediu — exige gestão."""
        nucleo.papel_developer()
        r = cliente_flow.post(
            "/api/v1/flows/flow-1/promotion",
            headers=CABECALHOS_REST,
            json={"target_scope": "account", "target_id": "acct-1"},
        )
        assert r.status_code == 403

    def test_sem_conta_ativa_e_recusado(self, cliente_flow):
        r = cliente_flow.get(
            "/api/v1/flows", headers={"authorization": token_de()}
        )
        assert r.status_code == 400


class TestGRPC:
    async def test_resolve_devolve_procedencia_estruturada(self, stub_flow):
        eff = await stub_flow.ResolveFlow(
            bff.ResolveFlowRequest(scope="project", scope_id="prj-1"), metadata=CONTA
        )
        assert list(eff.provenance.contributors) == ["account", "platform"]
        assert [(o.stage_key, o.scope) for o in eff.provenance.origins] == [
            ("contexto", "platform"),
            ("spec", "account"),
        ]
        assert eff.provenance.sentence == RASTRO

    async def test_fluxo_ausente_nao_e_fluxo_vazio(self, stub_flow, fluxos):
        """Nenhum nível declarou fluxo: o campo não vem. Um fluxo zerado diria
        que existe um fluxo sem nome e sem etapas."""
        fluxos.ResolveFlow.devolve(workflow_pb2.EffectiveFlow(resolved_from=""))
        eff = await stub_flow.ResolveFlow(
            bff.ResolveFlowRequest(scope="account"), metadata=CONTA
        )
        assert not eff.HasField("flow")

    async def test_developer_nao_promove_fluxo(self, stub_flow, nucleo):
        nucleo.papel_developer()
        with pytest.raises(grpc.aio.AioRpcError) as e:
            await stub_flow.PromoteFlow(
                bff.PromoteFlowRequest(flow_id="flow-1", target_scope="account"),
                metadata=CONTA,
            )
        assert e.value.code() == grpc.StatusCode.PERMISSION_DENIED

    async def test_sem_token_e_unauthenticated(self, stub_flow):
        with pytest.raises(grpc.aio.AioRpcError) as e:
            await stub_flow.ListFlows(
                bff.ListFlowsRequest(), metadata=metadados_de(None)
            )
        assert e.value.code() == grpc.StatusCode.UNAUTHENTICATED

    async def test_erro_do_nucleo_atravessa_com_o_status_dele(self, stub_flow, fluxos):
        fluxos.GetFlow.falha_com(grpc.StatusCode.NOT_FOUND, "fluxo não encontrado")
        with pytest.raises(grpc.aio.AioRpcError) as e:
            await stub_flow.GetFlow(bff.GetFlowRequest(id="sumiu"), metadata=CONTA)
        assert e.value.code() == grpc.StatusCode.NOT_FOUND


class TestParidadeEntreTransportes:
    """Uma função, dois adaptadores — e a prova de que continua assim."""

    async def test_fluxo_efetivo_igual_nas_duas_portas(self, cliente_flow, stub_flow):
        rest = cliente_flow.get(
            "/api/v1/flows/effective?scope=project&scope_id=prj-1",
            headers=CABECALHOS_REST,
        ).json()
        grpc_resp = await stub_flow.ResolveFlow(
            bff.ResolveFlowRequest(scope="project", scope_id="prj-1"), metadata=CONTA
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
