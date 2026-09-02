"""Demanda nos dois transportes, contra um núcleo fake.

O teste que mais importa aqui é o de paridade: é ele que impede alguém de
reimplementar um caso de uso no servicer (ou no router) sem ninguém notar na
revisão. Os outros protegem as duas coisas que a borda ACRESCENTA — os
derivados de "onde a demand está" e o cockpit numa call — e a disciplina de
ausente ≠ zerado, que aqui aparece em data de etapa e em ficha de agente.

Os doubles e as fixtures vivem NESTE arquivo, e não no conftest: há outros
agentes escrevendo neste repositório, e conftest é território compartilhado. O
que já existe lá (token, metadata, FakeCall, o núcleo de identidade) é
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
from tests.conftest import PROJECT, FakeCall, metadata_for, token_for

ACCOUNT = metadata_for(token_for())
REST_HEADERS = {"authorization": token_for(), "x-account-id": "acct-1"}


def viewer_role(core) -> None:
    """Rebaixa o ator a viewer.

    O role é resolvido UMA vez, no login, a partir de ListMemberships — então
    rebaixar é trocar o que esse RPC responde, não um atalho no context.
    Viewer (e não developer) porque no ciclo de trabalho developer ESCREVE: é
    ele quem toca a demand.
    """
    core.ListMemberships = FakeCall(
        identity_pb2.ListMembershipsResponse(
            memberships=[
                identity_pb2.Membership(
                    id="m-1",
                    user=common_pb2.UserRef(id=core.user_id),
                    account=common_pb2.AccountRef(id=core.account.id),
                    role=identity_pb2.ROLE_VIEWER,
                )
            ]
        )
    )


class DemandasFalsas:
    """Núcleo fake de demand.

    A demand tem três etapas de propósito: uma concluída, uma correndo com
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
        context = d.stages.add(
            key="context",
            name="Contexto",
            type=workflow_pb2.STAGE_TYPE_CONTEXT,
            status=demand_pb2.STAGE_STATUS_DONE,
            gate=workflow_pb2.GATE_NONE,
        )
        context.started_at.FromDatetime(datetime(2026, 8, 25, 9, 0))
        context.finished_at.FromDatetime(datetime(2026, 8, 25, 9, 30))
        context.artifacts.add(
            id="art-1",
            kind=workflow_pb2.ARTIFACT_KIND_DOCUMENT,
            name="context.md",
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
        self.demand = d

        principal = demand_pb2.Thread(
            id="th-1", demand=common_pb2.DemandRef(id="dem-1"), key="principal"
        )
        principal.card.CopyFrom(
            demand_pb2.AgentCard(
                purpose="tocar a demand",
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

        self.ListDemands = FakeCall(
            demand_pb2.ListDemandsResponse(
                demands=[d], page=common_pb2.PageResponse(next_token="pag-2", total=1)
            )
        )
        self.GetDemand = FakeCall(d)
        self.StartDemand = FakeCall(d)
        self.AdvanceStage = FakeCall(d.stages[2])
        self.DecideGate = FakeCall(d.stages[1])
        self.ListThreads = FakeCall(
            demand_pb2.ListThreadsResponse(threads=[principal, sem_ficha])
        )
        self.CreateThread = FakeCall(principal)
        self.PostMessage = FakeCall(
            demand_pb2.Message(
                id="msg-1",
                thread_id="th-1",
                author=common_pb2.ActorRef(
                    kind=common_pb2.ActorRef.KIND_USER, id="u-1", name="Dev"
                ),
                text="pode seguir",
            )
        )
        finding = demand_pb2.Finding(
            id="fnd-1", thread_id="th-2", title="índice ausente em requests"
        )
        finding.payload.update({"tabela": "requests", "linhas": 4200})
        self.finding = finding
        self.PublishFinding = FakeCall(finding)
        # A leitura que o núcleo passou a expor (P-19). Antes dela o cockpit
        # devolvia lista vazia com uma bandeira dizendo "não dá para saber".
        self.ListFindings = FakeCall(
            demand_pb2.ListFindingsResponse(findings=[finding])
        )


@pytest.fixture
def demands(core, monkeypatch):
    fake = DemandasFalsas()
    monkeypatch.setattr(stubs, "demand_stub", lambda: fake)
    return fake


def _app_com_rotas():
    """O app com as rotas de demand.

    `app/main.py` não é deste agente: enquanto o registro não chega lá, o teste
    monta o app e acrescenta o router. O `if` deixa o teste continuar válido
    depois do registro, sem rota duplicada.
    """
    app = create_app()
    if not any(getattr(r, "path", "") == "/api/v1/demands" for r in app.routes):
        app.include_router(rotas.router)
    return app


@pytest.fixture
def client_dem(demands):
    with TestClient(_app_com_rotas()) as c:
        yield c


@pytest.fixture
async def stub_dem(demands):
    """Servidor gRPC real, em porta efêmera, com os MESMOS interceptores da porta
    de produção — é o que prova que o ContextVar sobrevive até o servicer.

    Próprio (e não o `grpc_server` do conftest) porque `app/grpcapi/server.py`
    ainda não registra este servicer, e esse arquivo não é deste agente.
    """
    server = grpc.aio.server(
        interceptors=(
            LoggingInterceptor(),
            ErrorInterceptor(),
            AuthInterceptor(FirebaseVerifier(PROJECT), CoreResolver()),
        )
    )
    bff_grpc.add_DemandServiceServicer_to_server(DemandServicer(), server)
    porta = server.add_insecure_port("127.0.0.1:0")
    await server.start()
    try:
        async with grpc.aio.insecure_channel(f"127.0.0.1:{porta}") as channel:
            yield bff_grpc.DemandServiceStub(channel)
    finally:
        await server.stop(0)


class TestREST:
    def test_cockpit_traz_demanda_threads_e_achados_numa_resposta(
        self, client_dem, demands
    ):
        r = client_dem.get("/api/v1/demands/dem-1/cockpit", headers=REST_HEADERS)
        assert r.status_code == 200
        body = r.json()
        assert body["demand"]["external_key"] == "SUOPT-1315"
        assert [t["key"] for t in body["threads"]] == ["principal", "forense-db"]
        assert [f["title"] for f in body["findings"]] == ["índice ausente em requests"]
        assert body["findings"][0]["payload"] == {"tabela": "requests", "linhas": 4200}
        # TRÊS calls ao núcleo, uma response ao client.
        assert len(demands.GetDemand.calls) == 1
        assert len(demands.ListThreads.calls) == 1
        assert len(demands.ListFindings.calls) == 1
        assert demands.ListFindings.requests[0].demand_id == "dem-1"
        # Sem thread: o board é o da demand inteira.
        assert demands.ListFindings.requests[0].thread_id == ""

    def test_lista_vazia_de_achados_agora_significa_lista_vazia(
        self, client_dem, demands
    ):
        """O fim de `findings_available`.

        Ele existia porque o contrato do núcleo não tinha leitura de findings: a
        lista vinha vazia e a bandeira separava "não tem findings" de "não dá
        para saber". Com `ListFindings` só resta o primeiro caso — e uma
        bandeira constante seria ruído que um dia alguém lê ao contrário.
        """
        demands.ListFindings.returns(demand_pb2.ListFindingsResponse())
        body = client_dem.get(
            "/api/v1/demands/dem-1/cockpit", headers=REST_HEADERS
        ).json()
        assert body["findings"] == []
        assert "findings_available" not in body

    def test_falha_ao_ler_achados_derruba_o_cockpit_inteiro(
        self, client_dem, demands
    ):
        """Resposta pela metade sem dizer que está pela metade é pior que err.

        É o outro lado da mesma decisão: sem bandeira para dizer "não deu",
        quem não consegue ler os findings returns o status do núcleo, e não um
        cockpit que parece completo com um pedaço faltando.
        """
        demands.ListFindings.fails_with(grpc.StatusCode.PERMISSION_DENIED)
        r = client_dem.get("/api/v1/demands/dem-1/cockpit", headers=REST_HEADERS)
        assert r.status_code == 403

    def test_etapa_que_nao_comecou_vem_nula_nao_zerada(self, client_dem):
        """Ausente e zerado são coisas diferentes.

        Um início zerado viraria 1º de janeiro de 1970 na screen — e data errada
        é pior que data nenhuma.
        """
        d = client_dem.get("/api/v1/demands/dem-1", headers=REST_HEADERS).json()
        context, spec, impl = d["stages"]
        assert context["started_at"] is not None
        assert context["finished_at"] is not None
        assert spec["started_at"] is not None
        assert spec["finished_at"] is None
        assert impl["started_at"] is None

    def test_derivados_dizem_onde_a_demanda_esta(self, client_dem):
        """A primeira etapa não concluída, e quem espera gente.

        É a account que o cockpit, o dop-cli e o agente fariam cada um do seu
        jeito — e é assim que a lista e a screen passam a discordar.
        """
        d = client_dem.get("/api/v1/demands/dem-1", headers=REST_HEADERS).json()
        assert d["current_stage_key"] == "spec"
        assert d["awaiting_decision"] is True
        assert d["blocked"] is False
        # A etapa que espera é a do portão humano, e só ela.
        assert [s["awaiting_decision"] for s in d["stages"]] == [False, True, False]

    def test_thread_sem_ficha_vem_nula(self, client_dem):
        threads = client_dem.get(
            "/api/v1/demands/dem-1/threads", headers=REST_HEADERS
        ).json()
        com, sem = threads
        assert com["card"]["model"] == "opus"
        assert sem["card"] is None

    def test_lista_repassa_o_token_da_proxima_pagina(self, client_dem):
        r = client_dem.get("/api/v1/demands", headers=REST_HEADERS)
        assert r.json()["next_page_token"] == "pag-2"

    def test_viewer_nao_inicia_demanda(self, client_dem, core):
        viewer_role(core)
        r = client_dem.post(
            "/api/v1/demands",
            headers=REST_HEADERS,
            json={"project_id": "prj-1", "external_key": "SUOPT-1315"},
        )
        assert r.status_code == 403

    def test_escrita_carrega_idempotencia(self, client_dem, demands):
        """Sem key, o retry do channel abre duas demands para o mesmo card."""
        client_dem.post(
            "/api/v1/demands",
            headers=REST_HEADERS,
            json={"project_id": "prj-1", "external_key": "SUOPT-1315"},
        )
        assert demands.StartDemand.requests[0].idempotency_key != ""

    def test_status_de_etapa_inventado_e_recusado(self, client_dem):
        """O vocabulário é fechado, e a recusa mora no caso de uso — por isso
        vale igual nas duas portas."""
        r = client_dem.post(
            "/api/v1/demands/dem-1/stages/spec/advance",
            headers=REST_HEADERS,
            json={"status": "quase-la"},
        )
        assert r.status_code == 422

    def test_sem_conta_ativa_e_recusado(self, client_dem):
        r = client_dem.get(
            "/api/v1/demands/dem-1", headers={"authorization": token_for()}
        )
        assert r.status_code == 400


class TestGRPC:
    async def test_cockpit(self, stub_dem):
        resp = await stub_dem.GetDemandCockpit(
            bff.GetDemandCockpitRequest(demand_id="dem-1"), metadata=ACCOUNT
        )
        assert resp.demand.external_key == "SUOPT-1315"
        assert [t.key for t in resp.threads] == ["principal", "forense-db"]
        assert [f.title for f in resp.findings] == ["índice ausente em requests"]

    async def test_o_campo_findings_available_nao_existe_mais(self, stub_dem):
        """Removido do contrato da borda, com o número 4 reservado.

        Reservar impede que um campo novo herde o número da bandeira e seja
        lido por um client antigo como se ainda fosse ela — em silêncio.
        """
        campos = bff.DemandCockpit.DESCRIPTOR.fields_by_name
        assert "findings_available" not in campos
        assert all(f.number != 4 for f in campos.values())

    async def test_etapa_sem_data_nao_tem_campo(self, stub_dem):
        d = await stub_dem.GetDemand(bff.GetDemandRequest(id="dem-1"), metadata=ACCOUNT)
        context, spec, impl = d.stages
        assert context.HasField("started_at") and context.HasField("finished_at")
        assert spec.HasField("started_at")
        assert not spec.HasField("finished_at")
        assert not impl.HasField("started_at")

    async def test_thread_sem_ficha_nao_tem_campo(self, stub_dem):
        resp = await stub_dem.ListThreads(
            bff.ListThreadsRequest(demand_id="dem-1"), metadata=ACCOUNT
        )
        com, sem = resp.threads
        assert com.HasField("card")
        assert not sem.HasField("card")

    async def test_thread_criada_sem_ficha_nao_manda_ficha_ao_nucleo(
        self, stub_dem, demands
    ):
        """Ficha zerada declararia um agente sem propósito e sem orçamento."""
        await stub_dem.CreateThread(
            bff.CreateThreadRequest(demand_id="dem-1", key="logs"), metadata=ACCOUNT
        )
        assert not demands.CreateThread.requests[0].HasField("card")

    async def test_cliente_pode_mandar_a_propria_idempotencia(self, stub_dem, demands):
        """No gRPC quem sabe que está retentando é o client; o REST não tem
        onde carregar a key e recebe uma nossa."""
        await stub_dem.StartDemand(
            bff.StartDemandRequest(
                project_id="prj-1", external_key="S-1", idempotency_key="minha-key"
            ),
            metadata=ACCOUNT,
        )
        assert demands.StartDemand.requests[0].idempotency_key == "minha-key"

    async def test_viewer_nao_decide_portao(self, stub_dem, core):
        viewer_role(core)
        with pytest.raises(grpc.aio.AioRpcError) as e:
            await stub_dem.DecideGate(
                bff.DecideGateRequest(demand_id="dem-1", stage_key="spec", approved=True),
                metadata=ACCOUNT,
            )
        assert e.value.code() == grpc.StatusCode.PERMISSION_DENIED

    async def test_sem_token_e_unauthenticated(self, stub_dem):
        with pytest.raises(grpc.aio.AioRpcError) as e:
            await stub_dem.GetDemand(
                bff.GetDemandRequest(id="dem-1"), metadata=metadata_for(None)
            )
        assert e.value.code() == grpc.StatusCode.UNAUTHENTICATED


class TestParidadeEntreTransportes:
    """Uma função, dois adaptadores — e a prova de que continua assim."""

    async def test_cockpit_igual_nas_duas_portas(self, client_dem, stub_dem):
        rest = client_dem.get(
            "/api/v1/demands/dem-1/cockpit", headers=REST_HEADERS
        ).json()
        grpc_resp = await stub_dem.GetDemandCockpit(
            bff.GetDemandCockpitRequest(demand_id="dem-1"), metadata=ACCOUNT
        )

        assert grpc_resp.demand.id == rest["demand"]["id"]
        # Os DERIVADOS são o ponto: se um adaptador os recalculasse, seria aqui
        # que a diferença apareceria.
        assert grpc_resp.demand.current_stage_key == rest["demand"]["current_stage_key"]
        assert grpc_resp.demand.awaiting_decision == rest["demand"]["awaiting_decision"]
        assert grpc_resp.demand.blocked == rest["demand"]["blocked"]
        assert [t.key for t in grpc_resp.threads] == [t["key"] for t in rest["threads"]]
        assert [f.id for f in grpc_resp.findings] == [f["id"] for f in rest["findings"]]
        assert dict(grpc_resp.findings[0].payload) == rest["findings"][0]["payload"]

        # E o que é opcional, que é onde ausente≠zerado pode divergir entre pontas.
        for e_grpc, e_rest in zip(grpc_resp.demand.stages, rest["demand"]["stages"], strict=True):
            assert e_grpc.HasField("started_at") == (e_rest["started_at"] is not None)
            assert e_grpc.HasField("finished_at") == (e_rest["finished_at"] is not None)
        for t_grpc, t_rest in zip(grpc_resp.threads, rest["threads"], strict=True):
            assert t_grpc.HasField("card") == (t_rest["card"] is not None)
