"""Entrega nos dois transportes, contra um núcleo falso.

O centro deste arquivo é a **recusa por falta de verde** (ADR-0007): o núcleo
recusa a entrada na fila dizendo, item por item, o que falta, e essa lista é a
parte útil da resposta. Os testes cobrem os três jeitos de estragá-la — engolir
(virar um "não deu"), achatar (virar uma frase só) e engolir DEMAIS (tratar
qualquer erro do núcleo como recusa).

Os duplos e as fixtures vivem NESTE arquivo (conftest é território
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
from tests.conftest import PROJECT, ChamadaFalsa, metadados_de, token_de

CONTA = metadados_de(token_de())
CABECALHOS_REST = {"authorization": token_de(), "x-account-id": "acct-1"}

# A frase exata que o núcleo escreve ao recusar a entrada na fila
# (internal/domain/delivery/service.go + Evidence.Missing).
RECUSA = (
    "a fila de merge recusa entrada sem evidência de verde do commit abc1234 (ADR-0007): "
    "nenhuma execução de aceitação aprovada para o commit abc1234 (ADR-0007 §1); "
    "falta o parecer do crítico para o commit abc1234 (ADR-0007 §3)"
)


def papel_viewer(nucleo) -> None:
    """Rebaixa o ator a viewer — quem lê a entrega, mas não a empurra.

    Trocando o que ListMemberships responde, que é de onde o papel sai no
    login; um atalho no contexto provaria menos do que parece.
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


class EntregasFalsas:
    """Núcleo falso de entrega."""

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
        entrada = delivery_pb2.MergeQueueEntry(
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

        self.pr, self.entrada, self.diretriz = pr, entrada, diretriz
        self.ListPullRequests = ChamadaFalsa(
            delivery_pb2.ListPullRequestsResponse(pull_requests=[pr])
        )
        self.GetMergeQueue = ChamadaFalsa(
            delivery_pb2.GetMergeQueueResponse(entries=[entrada])
        )
        self.EnqueueMerge = ChamadaFalsa(entrada)
        self.ListDirectives = ChamadaFalsa(
            delivery_pb2.ListDirectivesResponse(directives=[diretriz])
        )
        self.DecideDirective = ChamadaFalsa(diretriz)

    def sem_verde(self) -> None:
        """O núcleo recusa a entrada na fila, listando o que falta."""
        self.EnqueueMerge.falha_com(grpc.StatusCode.FAILED_PRECONDITION, RECUSA)


@pytest.fixture
def entregas(nucleo, monkeypatch):
    falso = EntregasFalsas()
    monkeypatch.setattr(stubs, "delivery_stub", lambda: falso)
    return falso


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
def cliente_ent(entregas):
    with TestClient(_app_com_rotas()) as c:
        yield c


@pytest.fixture
async def stub_ent(entregas):
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
    bff_grpc.add_DeliveryServiceServicer_to_server(DeliveryServicer(), servidor)
    porta = servidor.add_insecure_port("127.0.0.1:0")
    await servidor.start()
    try:
        async with grpc.aio.insecure_channel(f"127.0.0.1:{porta}") as canal:
            yield bff_grpc.DeliveryServiceStub(canal)
    finally:
        await servidor.stop(0)


class TestSemVerde:
    """A recusa da fila — o que o dev precisa ler, inteiro."""

    def test_rest_devolve_412_com_a_lista_do_que_falta(self, cliente_ent, entregas):
        entregas.sem_verde()
        r = cliente_ent.post(
            "/api/v1/repos/repo-1/merge-queue",
            headers=CABECALHOS_REST,
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
        (ADR-0007 §2) — por isso a recusa é campo, e não status de erro."""
        entregas.sem_verde()
        resp = await stub_ent.EnqueueMerge(
            bff.EnqueueMergeRequest(repo_id="repo-1", demand_id="dem-1"), metadata=CONTA
        )
        assert resp.HasField("refusal")
        assert not resp.HasField("entry")
        assert len(resp.refusal.missing) == 2

    def test_recusa_sem_lista_nao_inventa_itens(self, cliente_ent, entregas):
        """Nem toda precondição é sobre verde: PR já mergeado tem razão e mais
        nada. Uma lista de um item repetindo a razão seria ruído."""
        entregas.EnqueueMerge.falha_com(
            grpc.StatusCode.FAILED_PRECONDITION, "o PR da demanda dem-1 já foi mergeado"
        )
        r = cliente_ent.post(
            "/api/v1/repos/repo-1/merge-queue",
            headers=CABECALHOS_REST,
            json={"demand_id": "dem-1"},
        )
        assert r.status_code == 412
        assert r.json()["detail"] == {
            "reason": "o PR da demanda dem-1 já foi mergeado",
            "missing": [],
        }

    def test_outro_erro_do_nucleo_nao_vira_recusa(self, cliente_ent, entregas):
        """Um `except` largo transformaria NOT_FOUND em 'falta verde' — e o dev
        procuraria uma execução de teste para um repositório que não existe."""
        entregas.EnqueueMerge.falha_com(grpc.StatusCode.NOT_FOUND, "repositório não encontrado")
        r = cliente_ent.post(
            "/api/v1/repos/repo-1/merge-queue",
            headers=CABECALHOS_REST,
            json={"demand_id": "dem-1"},
        )
        assert r.status_code == 404

    def test_detalhe_de_5xx_do_nucleo_nao_vaza(self, cliente_ent, entregas):
        """Desviar o erro do caminho normal não pode desviar o redator: falha
        interna do núcleo pode carregar host, query ou credencial."""
        entregas.EnqueueMerge.falha_com(grpc.StatusCode.INTERNAL, "dsn=postgres://user:senha@db")
        r = cliente_ent.post(
            "/api/v1/repos/repo-1/merge-queue",
            headers=CABECALHOS_REST,
            json={"demand_id": "dem-1"},
        )
        assert r.status_code == 500
        assert r.json()["detail"] == "internal error"

    async def test_paridade_da_recusa_entre_as_portas(self, cliente_ent, stub_ent, entregas):
        """A lista é a MESMA nos dois transportes; só o invólucro muda."""
        entregas.sem_verde()
        rest = cliente_ent.post(
            "/api/v1/repos/repo-1/merge-queue",
            headers=CABECALHOS_REST,
            json={"demand_id": "dem-1"},
        ).json()["detail"]
        grpc_resp = await stub_ent.EnqueueMerge(
            bff.EnqueueMergeRequest(repo_id="repo-1", demand_id="dem-1"), metadata=CONTA
        )
        assert list(grpc_resp.refusal.missing) == rest["missing"]
        assert grpc_resp.refusal.reason == rest["reason"]


class TestREST:
    def test_quadro_junta_prs_e_diretrizes(self, cliente_ent, entregas):
        r = cliente_ent.get(
            "/api/v1/delivery/board?project_id=prj-1", headers=CABECALHOS_REST
        )
        assert r.status_code == 200
        corpo = r.json()
        assert [p["id"] for p in corpo["pull_requests"]] == ["pr-1"]
        assert [d["kind"] for d in corpo["directives"]] == ["cherry_pick"]
        # Duas chamadas ao núcleo, uma resposta ao cliente.
        assert len(entregas.ListPullRequests.chamadas) == 1
        assert len(entregas.ListDirectives.chamadas) == 1

    def test_revisao_pendente_vem_contada(self, cliente_ent):
        """O número que ordena a caixa de atenção sai pronto — contar em cada
        cliente é a mesma regra escrita três vezes."""
        prs = cliente_ent.get(
            "/api/v1/pull-requests?demand_id=dem-1", headers=CABECALHOS_REST
        ).json()
        assert prs[0]["pending_reviews"] == 2

    def test_fila_e_de_um_repositorio(self, cliente_ent, entregas):
        r = cliente_ent.get("/api/v1/repos/repo-1/merge-queue", headers=CABECALHOS_REST)
        assert r.status_code == 200
        assert r.json()[0]["state"] == "queued"
        assert entregas.GetMergeQueue.pedidos[0].repo_id == "repo-1"

    def test_entrada_na_fila_carrega_idempotencia(self, cliente_ent, entregas):
        r = cliente_ent.post(
            "/api/v1/repos/repo-1/merge-queue",
            headers=CABECALHOS_REST,
            json={"demand_id": "dem-1"},
        )
        assert r.status_code == 201
        assert r.json()["position"] == 1
        assert entregas.EnqueueMerge.pedidos[0].idempotency_key != ""

    def test_payload_da_diretriz_atravessa_inteiro(self, cliente_ent):
        """O formato de cada tipo de diretriz é do techlead (ADR-0015): a borda
        repassa, não interpreta."""
        d = cliente_ent.get(
            "/api/v1/directives?project_id=prj-1", headers=CABECALHOS_REST
        ).json()
        assert d[0]["payload"] == {"de": "dem-0", "para": "dem-1"}
        assert d[0]["decided_by_name"] == "Dev"

    def test_viewer_nao_empurra_a_fila(self, cliente_ent, nucleo):
        papel_viewer(nucleo)
        r = cliente_ent.post(
            "/api/v1/repos/repo-1/merge-queue",
            headers=CABECALHOS_REST,
            json={"demand_id": "dem-1"},
        )
        assert r.status_code == 403

    def test_sem_conta_ativa_e_recusado(self, cliente_ent):
        r = cliente_ent.get(
            "/api/v1/repos/repo-1/merge-queue", headers={"authorization": token_de()}
        )
        assert r.status_code == 400


class TestGRPC:
    async def test_quadro_de_entrega(self, stub_ent):
        resp = await stub_ent.GetDeliveryBoard(
            bff.GetDeliveryBoardRequest(project_id="prj-1"), metadata=CONTA
        )
        assert [p.id for p in resp.pull_requests] == ["pr-1"]
        assert resp.pull_requests[0].pending_reviews == 2
        assert resp.directives[0].kind == bff.Directive.KIND_CHERRY_PICK

    async def test_entrada_na_fila_vem_como_entry(self, stub_ent):
        resp = await stub_ent.EnqueueMerge(
            bff.EnqueueMergeRequest(repo_id="repo-1", demand_id="dem-1"), metadata=CONTA
        )
        assert resp.HasField("entry")
        assert not resp.HasField("refusal")
        assert resp.entry.state == bff.MergeQueueEntry.STATE_QUEUED

    async def test_cliente_pode_mandar_a_propria_idempotencia(self, stub_ent, entregas):
        await stub_ent.EnqueueMerge(
            bff.EnqueueMergeRequest(
                repo_id="repo-1", demand_id="dem-1", idempotency_key="minha-chave"
            ),
            metadata=CONTA,
        )
        assert entregas.EnqueueMerge.pedidos[0].idempotency_key == "minha-chave"

    async def test_viewer_nao_decide_diretriz(self, stub_ent, nucleo):
        papel_viewer(nucleo)
        with pytest.raises(grpc.aio.AioRpcError) as e:
            await stub_ent.DecideDirective(
                bff.DecideDirectiveRequest(directive_id="dir-1"), metadata=CONTA
            )
        assert e.value.code() == grpc.StatusCode.PERMISSION_DENIED

    async def test_sem_token_e_unauthenticated(self, stub_ent):
        with pytest.raises(grpc.aio.AioRpcError) as e:
            await stub_ent.GetMergeQueue(
                bff.GetMergeQueueRequest(repo_id="repo-1"), metadata=metadados_de(None)
            )
        assert e.value.code() == grpc.StatusCode.UNAUTHENTICATED


class TestParidadeEntreTransportes:
    """Uma função, dois adaptadores — e a prova de que continua assim."""

    async def test_quadro_igual_nas_duas_portas(self, cliente_ent, stub_ent):
        rest = cliente_ent.get(
            "/api/v1/delivery/board?project_id=prj-1", headers=CABECALHOS_REST
        ).json()
        grpc_resp = await stub_ent.GetDeliveryBoard(
            bff.GetDeliveryBoardRequest(project_id="prj-1"), metadata=CONTA
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
