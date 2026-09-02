"""Entrega nos dois transportes, contra um núcleo fake.

O centro deste arquivo é a **recusa por falta de verde** (ADR-0007): o núcleo
recusa a entry na queue dizendo, item por item, o que falta, e essa lista é a
parte útil da response. Os testes cobrem os três jeitos de estragá-la — engolir
(virar um "não deu"), achatar (virar uma frase só) e engolir DEMAIS (tratar
qualquer err do núcleo como recusa).

Os doubles e as fixtures vivem NESTE arquivo (conftest é território
compartilhado); o que já existe lá é importado.
"""

import grpc
import pytest
from fastapi.testclient import TestClient

from app.coreclient import stubs
from app.coreclient.gen.dop.v1 import common_pb2, delivery_pb2, identity_pb2
from app.coreclient.resolver import CoreResolver
from app.grpcapi.delivery import DeliveryServicer
from app.grpcapi.gen.dop.bff.v1 import delivery_pb2 as bff
from app.grpcapi.gen.dop.bff.v1 import delivery_pb2_grpc as bff_grpc
from app.grpcapi.interceptors import AuthInterceptor, ErrorInterceptor, LoggingInterceptor
from app.main import create_app
from app.platform.security.firebase import FirebaseVerifier
from app.routers import delivery as rotas
from tests.conftest import PROJECT, FakeCall, metadata_for, token_for

ACCOUNT = metadata_for(token_for())
REST_HEADERS = {"authorization": token_for(), "x-account-id": "acct-1"}

# A frase exata que o núcleo escreve ao recusar a entry na queue
# (internal/domain/delivery/service.go + Evidence.Missing).
RECUSA = (
    "a queue de merge recusa entry sem evidência de verde do commit abc1234 (ADR-0007): "
    "nenhuma execução de aceitação aprovada para o commit abc1234 (ADR-0007 §1); "
    "falta o parecer do crítico para o commit abc1234 (ADR-0007 §3)"
)


def viewer_role(core) -> None:
    """Rebaixa o ator a viewer — quem lê a entrega, mas não a empurra.

    Trocando o que ListMemberships responde, que é de onde o role sai no
    login; um atalho no context provaria menos do que parece.
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


class EntregasFalsas:
    """Núcleo fake de entrega."""

    def __init__(self):
        pr = delivery_pb2.PullRequest(
            id="pr-1",
            demand=common_pb2.DemandRef(id="dem-1"),
            repo="acme/portal",
            source_branch="dop/dem-1",
            target_branch="main",
            url="https://git/acme/portal/pull/7",
            reviewers=[
                delivery_pb2.Reviewer(name="Ana", initials="AS", status="approved"),
                delivery_pb2.Reviewer(name="Bruno", initials="BC", status="pending"),
                delivery_pb2.Reviewer(name="Caio", initials="CL", status="pending"),
            ],
        )
        entry = delivery_pb2.MergeQueueEntry(
            id="mq-1",
            repo_id="repo-1",
            demand=common_pb2.DemandRef(id="dem-1"),
            position=1,
            state=delivery_pb2.MergeQueueEntry.STATE_QUEUED,
            overlapping_files=["app/main.py"],
        )
        diretriz = delivery_pb2.Directive(
            id="dir-1",
            project=common_pb2.ProjectRef(id="prj-1"),
            kind=delivery_pb2.Directive.KIND_CHERRY_PICK,
            decided_by=common_pb2.ActorRef(
                kind=common_pb2.ActorRef.KIND_USER, id="u-1", name="Dev"
            ),
        )
        diretriz.payload.update({"de": "dem-0", "para": "dem-1"})

        self.pr, self.entry, self.diretriz = pr, entry, diretriz
        self.ListPullRequests = FakeCall(
            delivery_pb2.ListPullRequestsResponse(pull_requests=[pr])
        )
        self.GetMergeQueue = FakeCall(
            delivery_pb2.GetMergeQueueResponse(entries=[entry])
        )
        self.EnqueueMerge = FakeCall(entry)
        self.ListDirectives = FakeCall(
            delivery_pb2.ListDirectivesResponse(directives=[diretriz])
        )
        self.DecideDirective = FakeCall(diretriz)

    def sem_verde(self) -> None:
        """O núcleo recusa a entry na queue, listando o que falta."""
        self.EnqueueMerge.fails_with(grpc.StatusCode.FAILED_PRECONDITION, RECUSA)


@pytest.fixture
def entregas(core, monkeypatch):
    fake = EntregasFalsas()
    monkeypatch.setattr(stubs, "delivery_stub", lambda: fake)
    return fake


def _app_com_rotas():
    """O app com as rotas de entrega.

    `app/main.py` não é deste agente: enquanto o registro não chega lá, o teste
    monta o app e acrescenta o router. O `if` deixa o teste continuar válido
    depois do registro, sem rota duplicada.
    """
    app = create_app()
    if not any(getattr(r, "path", "") == "/api/v1/delivery/board" for r in app.routes):
        app.include_router(rotas.router)
    return app


@pytest.fixture
def client_del(entregas):
    with TestClient(_app_com_rotas()) as c:
        yield c


@pytest.fixture
async def stub_ent(entregas):
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
    bff_grpc.add_DeliveryServiceServicer_to_server(DeliveryServicer(), server)
    porta = server.add_insecure_port("127.0.0.1:0")
    await server.start()
    try:
        async with grpc.aio.insecure_channel(f"127.0.0.1:{porta}") as channel:
            yield bff_grpc.DeliveryServiceStub(channel)
    finally:
        await server.stop(0)


class TestSemVerde:
    """A recusa da queue — o que o dev precisa ler, inteiro."""

    def test_rest_devolve_412_com_a_lista_do_que_falta(self, client_del, entregas):
        entregas.sem_verde()
        r = client_del.post(
            "/api/v1/repos/repo-1/merge-queue",
            headers=REST_HEADERS,
            json={"demand_id": "dem-1"},
        )
        assert r.status_code == 412
        detalhe = r.json()["detail"]
        assert detalhe["missing"] == [
            "nenhuma execução de aceitação aprovada para o commit abc1234 (ADR-0007 §1)",
            "falta o parecer do crítico para o commit abc1234 (ADR-0007 §3)",
        ]
        # A razão fica com o commit: sem ele, "falta verde" não diz de QUAL
        # código se está falando — e o verde é sempre sobre um commit.
        assert "abc1234" in detalhe["reason"]

    async def test_grpc_devolve_a_recusa_como_resposta(self, stub_ent, entregas):
        """Para o agente, 'ainda não, falta isto' é trabalho a fazer, não falha
        (ADR-0007 §2) — por isso a recusa é campo, e não status de err."""
        entregas.sem_verde()
        resp = await stub_ent.EnqueueMerge(
            bff.EnqueueMergeRequest(repo_id="repo-1", demand_id="dem-1"), metadata=ACCOUNT
        )
        assert resp.HasField("refusal")
        assert not resp.HasField("entry")
        assert len(resp.refusal.missing) == 2

    def test_recusa_sem_lista_nao_inventa_itens(self, client_del, entregas):
        """Nem toda precondição é sobre verde: PR já mergeado tem razão e mais
        nada. Uma lista de um item repetindo a razão seria ruído."""
        entregas.EnqueueMerge.fails_with(
            grpc.StatusCode.FAILED_PRECONDITION, "o PR da demand dem-1 já foi mergeado"
        )
        r = client_del.post(
            "/api/v1/repos/repo-1/merge-queue",
            headers=REST_HEADERS,
            json={"demand_id": "dem-1"},
        )
        assert r.status_code == 412
        assert r.json()["detail"] == {
            "reason": "o PR da demand dem-1 já foi mergeado",
            "missing": [],
        }

    def test_outro_erro_do_nucleo_nao_vira_recusa(self, client_del, entregas):
        """Um `except` largo transformaria NOT_FOUND em 'falta verde' — e o dev
        procuraria uma execução de teste para um repositório que não existe."""
        entregas.EnqueueMerge.fails_with(grpc.StatusCode.NOT_FOUND, "repositório não encontrado")
        r = client_del.post(
            "/api/v1/repos/repo-1/merge-queue",
            headers=REST_HEADERS,
            json={"demand_id": "dem-1"},
        )
        assert r.status_code == 404

    def test_detalhe_de_5xx_do_nucleo_nao_vaza(self, client_del, entregas):
        """Desviar o err do caminho normal não pode desviar o redator: falha
        interna do núcleo pode carregar host, query ou credencial."""
        entregas.EnqueueMerge.fails_with(grpc.StatusCode.INTERNAL, "dsn=postgres://user:senha@db")
        r = client_del.post(
            "/api/v1/repos/repo-1/merge-queue",
            headers=REST_HEADERS,
            json={"demand_id": "dem-1"},
        )
        assert r.status_code == 500
        assert r.json()["detail"] == "internal error"

    async def test_paridade_da_recusa_entre_as_portas(self, client_del, stub_ent, entregas):
        """A lista é a MESMA nos dois transportes; só o invólucro muda."""
        entregas.sem_verde()
        rest = client_del.post(
            "/api/v1/repos/repo-1/merge-queue",
            headers=REST_HEADERS,
            json={"demand_id": "dem-1"},
        ).json()["detail"]
        grpc_resp = await stub_ent.EnqueueMerge(
            bff.EnqueueMergeRequest(repo_id="repo-1", demand_id="dem-1"), metadata=ACCOUNT
        )
        assert list(grpc_resp.refusal.missing) == rest["missing"]
        assert grpc_resp.refusal.reason == rest["reason"]


class TestREST:
    def test_quadro_junta_prs_e_diretrizes(self, client_del, entregas):
        r = client_del.get(
            "/api/v1/delivery/board?project_id=prj-1", headers=REST_HEADERS
        )
        assert r.status_code == 200
        body = r.json()
        assert [p["id"] for p in body["pull_requests"]] == ["pr-1"]
        assert [d["kind"] for d in body["directives"]] == ["cherry_pick"]
        # Duas calls ao núcleo, uma response ao client.
        assert len(entregas.ListPullRequests.calls) == 1
        assert len(entregas.ListDirectives.calls) == 1

    def test_revisao_pendente_vem_contada(self, client_del):
        """O número que ordena a box de atenção sai pronto — contar em cada
        client é a mesma regra escrita três vezes."""
        prs = client_del.get(
            "/api/v1/pull-requests?demand_id=dem-1", headers=REST_HEADERS
        ).json()
        assert prs[0]["pending_reviews"] == 2

    def test_fila_e_de_um_repositorio(self, client_del, entregas):
        r = client_del.get("/api/v1/repos/repo-1/merge-queue", headers=REST_HEADERS)
        assert r.status_code == 200
        assert r.json()[0]["state"] == "queued"
        assert entregas.GetMergeQueue.requests[0].repo_id == "repo-1"

    def test_entrada_na_fila_carrega_idempotencia(self, client_del, entregas):
        r = client_del.post(
            "/api/v1/repos/repo-1/merge-queue",
            headers=REST_HEADERS,
            json={"demand_id": "dem-1"},
        )
        assert r.status_code == 201
        assert r.json()["position"] == 1
        assert entregas.EnqueueMerge.requests[0].idempotency_key != ""

    def test_payload_da_diretriz_atravessa_inteiro(self, client_del):
        """O formato de cada tipo de diretriz é do techlead (ADR-0015): a borda
        repassa, não interpreta."""
        d = client_del.get(
            "/api/v1/directives?project_id=prj-1", headers=REST_HEADERS
        ).json()
        assert d[0]["payload"] == {"de": "dem-0", "para": "dem-1"}
        assert d[0]["decided_by_name"] == "Dev"

    def test_viewer_nao_empurra_a_fila(self, client_del, core):
        viewer_role(core)
        r = client_del.post(
            "/api/v1/repos/repo-1/merge-queue",
            headers=REST_HEADERS,
            json={"demand_id": "dem-1"},
        )
        assert r.status_code == 403

    def test_sem_conta_ativa_e_recusado(self, client_del):
        r = client_del.get(
            "/api/v1/repos/repo-1/merge-queue", headers={"authorization": token_for()}
        )
        assert r.status_code == 400


class TestGRPC:
    async def test_quadro_de_entrega(self, stub_ent):
        resp = await stub_ent.GetDeliveryBoard(
            bff.GetDeliveryBoardRequest(project_id="prj-1"), metadata=ACCOUNT
        )
        assert [p.id for p in resp.pull_requests] == ["pr-1"]
        assert resp.pull_requests[0].pending_reviews == 2
        assert resp.directives[0].kind == bff.Directive.KIND_CHERRY_PICK

    async def test_entrada_na_fila_vem_como_entry(self, stub_ent):
        resp = await stub_ent.EnqueueMerge(
            bff.EnqueueMergeRequest(repo_id="repo-1", demand_id="dem-1"), metadata=ACCOUNT
        )
        assert resp.HasField("entry")
        assert not resp.HasField("refusal")
        assert resp.entry.state == bff.MergeQueueEntry.STATE_QUEUED

    async def test_cliente_pode_mandar_a_propria_idempotencia(self, stub_ent, entregas):
        await stub_ent.EnqueueMerge(
            bff.EnqueueMergeRequest(
                repo_id="repo-1", demand_id="dem-1", idempotency_key="minha-key"
            ),
            metadata=ACCOUNT,
        )
        assert entregas.EnqueueMerge.requests[0].idempotency_key == "minha-key"

    async def test_viewer_nao_decide_diretriz(self, stub_ent, core):
        viewer_role(core)
        with pytest.raises(grpc.aio.AioRpcError) as e:
            await stub_ent.DecideDirective(
                bff.DecideDirectiveRequest(directive_id="dir-1"), metadata=ACCOUNT
            )
        assert e.value.code() == grpc.StatusCode.PERMISSION_DENIED

    async def test_sem_token_e_unauthenticated(self, stub_ent):
        with pytest.raises(grpc.aio.AioRpcError) as e:
            await stub_ent.GetMergeQueue(
                bff.GetMergeQueueRequest(repo_id="repo-1"), metadata=metadata_for(None)
            )
        assert e.value.code() == grpc.StatusCode.UNAUTHENTICATED


class TestParidadeEntreTransportes:
    """Uma função, dois adaptadores — e a prova de que continua assim."""

    async def test_quadro_igual_nas_duas_portas(self, client_del, stub_ent):
        rest = client_del.get(
            "/api/v1/delivery/board?project_id=prj-1", headers=REST_HEADERS
        ).json()
        grpc_resp = await stub_ent.GetDeliveryBoard(
            bff.GetDeliveryBoardRequest(project_id="prj-1"), metadata=ACCOUNT
        )

        assert [p.id for p in grpc_resp.pull_requests] == [
            p["id"] for p in rest["pull_requests"]
        ]
        # O derivado é o ponto: se um adaptador o recalculasse, seria aqui que
        # a diferença apareceria.
        assert [p.pending_reviews for p in grpc_resp.pull_requests] == [
            p["pending_reviews"] for p in rest["pull_requests"]
        ]
        assert [d.id for d in grpc_resp.directives] == [d["id"] for d in rest["directives"]]
