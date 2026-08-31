"""Demanda nos dois transportes, contra um núcleo falso.

O teste que mais importa aqui é o de paridade: é ele que impede alguém de
reimplementar um caso de uso no servicer (ou no router) sem ninguém notar na
revisão. Os outros protegem as duas coisas que a borda ACRESCENTA — os
derivados de "onde a demanda está" e o cockpit numa chamada — e a disciplina de
ausente ≠ zerado, que aqui aparece em data de etapa e em ficha de agente.

Os duplos e as fixtures vivem NESTE arquivo, e não no conftest: há outros
agentes escrevendo neste repositório, e conftest é território compartilhado. O
que já existe lá (token, metadados, ChamadaFalsa, o núcleo de identidade) é
importado.
"""

from datetime import datetime

import grpc
import pytest
from fastapi.testclient import TestClient

from app.coreclient import stubs
from app.coreclient.gen.dop.v1 import common_pb2, demand_pb2, identity_pb2, workflow_pb2
from app.coreclient.resolver import CoreResolver
from app.grpcapi.demand import DemandServicer
from app.grpcapi.gen.dop.bff.v1 import demand_pb2 as bff
from app.grpcapi.gen.dop.bff.v1 import demand_pb2_grpc as bff_grpc
from app.grpcapi.interceptors import AuthInterceptor, ErrorInterceptor, LoggingInterceptor
from app.main import create_app
from app.platform.security.firebase import FirebaseVerifier
from app.routers import demand as rotas
from tests.conftest import PROJECT, ChamadaFalsa, metadados_de, token_de

CONTA = metadados_de(token_de())
CABECALHOS_REST = {"authorization": token_de(), "x-account-id": "acct-1"}


def papel_viewer(nucleo) -> None:
    """Rebaixa o ator a viewer.

    O papel é resolvido UMA vez, no login, a partir de ListMemberships — então
    rebaixar é trocar o que esse RPC responde, não um atalho no contexto.
    Viewer (e não developer) porque no ciclo de trabalho developer ESCREVE: é
    ele quem toca a demanda.
    """
    nucleo.ListMemberships = ChamadaFalsa(
        identity_pb2.ListMembershipsResponse(
            memberships=[
                identity_pb2.Membership(
                    id="m-1",
                    user=common_pb2.UserRef(id=nucleo.user_id),
                    account=common_pb2.AccountRef(id=nucleo.conta.id),
                    role=identity_pb2.ROLE_VIEWER,
                )
            ]
        )
    )


class DemandasFalsas:
    """Núcleo falso de demanda.

    A demanda tem três etapas de propósito: uma concluída, uma correndo com
    portão humano (a que espera decisão) e uma que nem começou — sem data
    nenhuma, que é o caso onde ausente e zerado se confundem. As threads também
    são duas: uma com ficha de agente e outra SEM.
    """

    def __init__(self):
        d = demand_pb2.Demand(
            id="dem-1",
            project=common_pb2.ProjectRef(id="prj-1"),
            external_key="SUOPT-1315",
            title="Corrigir o cálculo de rateio",
            card_type="bug",
            provider_status="In Progress",
            dop_status=demand_pb2.DOP_STATUS_DOING,
            flow_id="flow-1",
            flow_version=2,
        )
        contexto = d.stages.add(
            key="contexto",
            name="Contexto",
            type=workflow_pb2.STAGE_TYPE_CONTEXT,
            status=demand_pb2.STAGE_STATUS_DONE,
            gate=workflow_pb2.GATE_NONE,
        )
        contexto.started_at.FromDatetime(datetime(2026, 8, 25, 9, 0))
        contexto.finished_at.FromDatetime(datetime(2026, 8, 25, 9, 30))
        contexto.artifacts.add(
            id="art-1",
            kind=workflow_pb2.ARTIFACT_KIND_DOCUMENT,
            name="contexto.md",
            object_ref="obj://ctx",
            version=1,
        )

        spec = d.stages.add(
            key="spec",
            name="Spec",
            type=workflow_pb2.STAGE_TYPE_SPEC,
            status=demand_pb2.STAGE_STATUS_RUNNING,
            gate=workflow_pb2.GATE_HUMAN,
        )
        spec.started_at.FromDatetime(datetime(2026, 8, 25, 10, 0))
        # finished_at fica AUSENTE: a etapa não terminou.

        # Nem começou: sem início e sem fim.
        d.stages.add(
            key="implementacao",
            name="Implementação",
            type=workflow_pb2.STAGE_TYPE_IMPLEMENTATION,
            status=demand_pb2.STAGE_STATUS_PENDING,
            gate=workflow_pb2.GATE_NONE,
        )
        self.demanda = d

        principal = demand_pb2.Thread(
            id="th-1", demand=common_pb2.DemandRef(id="dem-1"), key="principal"
        )
        principal.card.CopyFrom(
            demand_pb2.AgentCard(
                purpose="tocar a demanda",
                tools=["mcp:mysql"],
                model="opus",
                effort="high",
                budget_micros=5_000_000,
            )
        )
        # Thread SEM ficha: é o caso que distingue ausente de zerado.
        sem_ficha = demand_pb2.Thread(
            id="th-2",
            demand=common_pb2.DemandRef(id="dem-1"),
            key="forense-db",
            blocked=True,
        )
        self.thread, self.thread_sem_ficha = principal, sem_ficha

        self.ListDemands = ChamadaFalsa(
            demand_pb2.ListDemandsResponse(
                demands=[d], page=common_pb2.PageResponse(next_token="pag-2", total=1)
            )
        )
        self.GetDemand = ChamadaFalsa(d)
        self.StartDemand = ChamadaFalsa(d)
        self.AdvanceStage = ChamadaFalsa(d.stages[2])
        self.DecideGate = ChamadaFalsa(d.stages[1])
        self.ListThreads = ChamadaFalsa(
            demand_pb2.ListThreadsResponse(threads=[principal, sem_ficha])
        )
        self.CreateThread = ChamadaFalsa(principal)
        self.PostMessage = ChamadaFalsa(
            demand_pb2.Message(
                id="msg-1",
                thread_id="th-1",
                author=common_pb2.ActorRef(
                    kind=common_pb2.ActorRef.KIND_USER, id="u-1", name="Dev"
                ),
                text="pode seguir",
            )
        )
        achado = demand_pb2.Finding(
            id="fnd-1", thread_id="th-2", title="índice ausente em pedidos"
        )
        achado.payload.update({"tabela": "pedidos", "linhas": 4200})
        self.PublishFinding = ChamadaFalsa(achado)


@pytest.fixture
def demandas(nucleo, monkeypatch):
    falso = DemandasFalsas()
    monkeypatch.setattr(stubs, "demand_stub", lambda: falso)
    return falso


def _app_com_rotas():
    """O app com as rotas de demanda.

    `app/main.py` não é deste agente: enquanto o registro não chega lá, o teste
    monta o app e acrescenta o router. O `if` deixa o teste continuar válido
    depois do registro, sem rota duplicada.
    """
    app = create_app()
    if not any(getattr(r, "path", "") == "/api/v1/demands" for r in app.routes):
        app.include_router(rotas.router)
    return app


@pytest.fixture
def cliente_dem(demandas):
    with TestClient(_app_com_rotas()) as c:
        yield c


@pytest.fixture
async def stub_dem(demandas):
    """Servidor gRPC real, em porta efêmera, com os MESMOS interceptores da porta
    de produção — é o que prova que o ContextVar sobrevive até o servicer.

    Próprio (e não o `servidor_grpc` do conftest) porque `app/grpcapi/server.py`
    ainda não registra este servicer, e esse arquivo não é deste agente.
    """
    servidor = grpc.aio.server(
        interceptors=(
            LoggingInterceptor(),
            ErrorInterceptor(),
            AuthInterceptor(FirebaseVerifier(PROJECT), CoreResolver()),
        )
    )
    bff_grpc.add_DemandServiceServicer_to_server(DemandServicer(), servidor)
    porta = servidor.add_insecure_port("127.0.0.1:0")
    await servidor.start()
    try:
        async with grpc.aio.insecure_channel(f"127.0.0.1:{porta}") as canal:
            yield bff_grpc.DemandServiceStub(canal)
    finally:
        await servidor.stop(0)


class TestREST:
    def test_cockpit_traz_demanda_e_threads_numa_resposta(self, cliente_dem, demandas):
        r = cliente_dem.get("/api/v1/demands/dem-1/cockpit", headers=CABECALHOS_REST)
        assert r.status_code == 200
        corpo = r.json()
        assert corpo["demand"]["external_key"] == "SUOPT-1315"
        assert [t["key"] for t in corpo["threads"]] == ["principal", "forense-db"]
        # Duas chamadas ao núcleo, uma resposta ao cliente.
        assert len(demandas.GetDemand.chamadas) == 1
        assert len(demandas.ListThreads.chamadas) == 1

    def test_achado_indisponivel_nao_e_achado_nenhum(self, cliente_dem):
        """`dop.v1` não expõe leitura de achados.

        Enquanto não expuser, a lista vem vazia COM a bandeira dizendo que não
        dá para saber — "esta demanda não tem achados" seria uma afirmação que
        a borda não pode fazer.
        """
        corpo = cliente_dem.get(
            "/api/v1/demands/dem-1/cockpit", headers=CABECALHOS_REST
        ).json()
        assert corpo["findings"] == []
        assert corpo["findings_available"] is False

    def test_etapa_que_nao_comecou_vem_nula_nao_zerada(self, cliente_dem):
        """Ausente e zerado são coisas diferentes.

        Um início zerado viraria 1º de janeiro de 1970 na tela — e data errada
        é pior que data nenhuma.
        """
        d = cliente_dem.get("/api/v1/demands/dem-1", headers=CABECALHOS_REST).json()
        contexto, spec, impl = d["stages"]
        assert contexto["started_at"] is not None
        assert contexto["finished_at"] is not None
        assert spec["started_at"] is not None
        assert spec["finished_at"] is None
        assert impl["started_at"] is None

    def test_derivados_dizem_onde_a_demanda_esta(self, cliente_dem):
        """A primeira etapa não concluída, e quem espera gente.

        É a conta que o cockpit, o dop-cli e o agente fariam cada um do seu
        jeito — e é assim que a lista e a tela passam a discordar.
        """
        d = cliente_dem.get("/api/v1/demands/dem-1", headers=CABECALHOS_REST).json()
        assert d["current_stage_key"] == "spec"
        assert d["awaiting_decision"] is True
        assert d["blocked"] is False
        # A etapa que espera é a do portão humano, e só ela.
        assert [s["awaiting_decision"] for s in d["stages"]] == [False, True, False]

    def test_thread_sem_ficha_vem_nula(self, cliente_dem):
        threads = cliente_dem.get(
            "/api/v1/demands/dem-1/threads", headers=CABECALHOS_REST
        ).json()
        com, sem = threads
        assert com["card"]["model"] == "opus"
        assert sem["card"] is None

    def test_lista_repassa_o_token_da_proxima_pagina(self, cliente_dem):
        r = cliente_dem.get("/api/v1/demands", headers=CABECALHOS_REST)
        assert r.json()["next_page_token"] == "pag-2"

    def test_viewer_nao_inicia_demanda(self, cliente_dem, nucleo):
        papel_viewer(nucleo)
        r = cliente_dem.post(
            "/api/v1/demands",
            headers=CABECALHOS_REST,
            json={"project_id": "prj-1", "external_key": "SUOPT-1315"},
        )
        assert r.status_code == 403

    def test_escrita_carrega_idempotencia(self, cliente_dem, demandas):
        """Sem chave, o retry do canal abre duas demandas para o mesmo card."""
        cliente_dem.post(
            "/api/v1/demands",
            headers=CABECALHOS_REST,
            json={"project_id": "prj-1", "external_key": "SUOPT-1315"},
        )
        assert demandas.StartDemand.pedidos[0].idempotency_key != ""

    def test_status_de_etapa_inventado_e_recusado(self, cliente_dem):
        """O vocabulário é fechado, e a recusa mora no caso de uso — por isso
        vale igual nas duas portas."""
        r = cliente_dem.post(
            "/api/v1/demands/dem-1/stages/spec/advance",
            headers=CABECALHOS_REST,
            json={"status": "quase-la"},
        )
        assert r.status_code == 422

    def test_sem_conta_ativa_e_recusado(self, cliente_dem):
        r = cliente_dem.get(
            "/api/v1/demands/dem-1", headers={"authorization": token_de()}
        )
        assert r.status_code == 400


class TestGRPC:
    async def test_cockpit(self, stub_dem):
        resp = await stub_dem.GetDemandCockpit(
            bff.GetDemandCockpitRequest(demand_id="dem-1"), metadata=CONTA
        )
        assert resp.demand.external_key == "SUOPT-1315"
        assert [t.key for t in resp.threads] == ["principal", "forense-db"]
        assert resp.findings_available is False

    async def test_etapa_sem_data_nao_tem_campo(self, stub_dem):
        d = await stub_dem.GetDemand(bff.GetDemandRequest(id="dem-1"), metadata=CONTA)
        contexto, spec, impl = d.stages
        assert contexto.HasField("started_at") and contexto.HasField("finished_at")
        assert spec.HasField("started_at")
        assert not spec.HasField("finished_at")
        assert not impl.HasField("started_at")

    async def test_thread_sem_ficha_nao_tem_campo(self, stub_dem):
        resp = await stub_dem.ListThreads(
            bff.ListThreadsRequest(demand_id="dem-1"), metadata=CONTA
        )
        com, sem = resp.threads
        assert com.HasField("card")
        assert not sem.HasField("card")

    async def test_thread_criada_sem_ficha_nao_manda_ficha_ao_nucleo(
        self, stub_dem, demandas
    ):
        """Ficha zerada declararia um agente sem propósito e sem orçamento."""
        await stub_dem.CreateThread(
            bff.CreateThreadRequest(demand_id="dem-1", key="logs"), metadata=CONTA
        )
        assert not demandas.CreateThread.pedidos[0].HasField("card")

    async def test_cliente_pode_mandar_a_propria_idempotencia(self, stub_dem, demandas):
        """No gRPC quem sabe que está retentando é o cliente; o REST não tem
        onde carregar a chave e recebe uma nossa."""
        await stub_dem.StartDemand(
            bff.StartDemandRequest(
                project_id="prj-1", external_key="S-1", idempotency_key="minha-chave"
            ),
            metadata=CONTA,
        )
        assert demandas.StartDemand.pedidos[0].idempotency_key == "minha-chave"

    async def test_viewer_nao_decide_portao(self, stub_dem, nucleo):
        papel_viewer(nucleo)
        with pytest.raises(grpc.aio.AioRpcError) as e:
            await stub_dem.DecideGate(
                bff.DecideGateRequest(demand_id="dem-1", stage_key="spec", approved=True),
                metadata=CONTA,
            )
        assert e.value.code() == grpc.StatusCode.PERMISSION_DENIED

    async def test_sem_token_e_unauthenticated(self, stub_dem):
        with pytest.raises(grpc.aio.AioRpcError) as e:
            await stub_dem.GetDemand(
                bff.GetDemandRequest(id="dem-1"), metadata=metadados_de(None)
            )
        assert e.value.code() == grpc.StatusCode.UNAUTHENTICATED


class TestParidadeEntreTransportes:
    """Uma função, dois adaptadores — e a prova de que continua assim."""

    async def test_cockpit_igual_nas_duas_portas(self, cliente_dem, stub_dem):
        rest = cliente_dem.get(
            "/api/v1/demands/dem-1/cockpit", headers=CABECALHOS_REST
        ).json()
        grpc_resp = await stub_dem.GetDemandCockpit(
            bff.GetDemandCockpitRequest(demand_id="dem-1"), metadata=CONTA
        )

        assert grpc_resp.demand.id == rest["demand"]["id"]
        # Os DERIVADOS são o ponto: se um adaptador os recalculasse, seria aqui
        # que a diferença apareceria.
        assert grpc_resp.demand.current_stage_key == rest["demand"]["current_stage_key"]
        assert grpc_resp.demand.awaiting_decision == rest["demand"]["awaiting_decision"]
        assert grpc_resp.demand.blocked == rest["demand"]["blocked"]
        assert [t.key for t in grpc_resp.threads] == [t["key"] for t in rest["threads"]]
        assert grpc_resp.findings_available == rest["findings_available"]

        # E o que é opcional, que é onde ausente≠zerado pode divergir entre pontas.
        for e_grpc, e_rest in zip(grpc_resp.demand.stages, rest["demand"]["stages"], strict=True):
            assert e_grpc.HasField("started_at") == (e_rest["started_at"] is not None)
            assert e_grpc.HasField("finished_at") == (e_rest["finished_at"] is not None)
        for t_grpc, t_rest in zip(grpc_resp.threads, rest["threads"], strict=True):
            assert t_grpc.HasField("card") == (t_rest["card"] is not None)
