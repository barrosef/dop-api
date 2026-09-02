"""Conhecimento nos dois transportes, contra um núcleo fake.

Dois testes carregam o arquivo:

  - o de DESCARTE: o pacote de context é selecionado por orçamento (ADR-0012),
    e a borda tem de conseguir dizer "não coube tudo". O núcleo informa o
    descarte num `map<string,int32>` por camada, e o preenche sempre — mapa
    VAZIO é o núcleo calado, e vira `null`, não zeros, que AFIRMARIAM que nada
    ficou de fora;
  - o de vazamento de convenção interna: as keys `dop.body`/`dop.scope` do
    núcleo não podem atravessar para a screen. Ele é escrito procurando a key no
    body serializado INTEIRO, e não campo por campo — do jeito que
    `tests/test_resource.py` procura o segredo —, porque conferir campo a campo
    só pega o vazamento que alguém lembrou de imaginar.

Fakes e fixtures ficam AQUI, e não no `conftest.py`: há outros agentes
escrevendo neste repositório agora.
"""

import base64

import grpc
import pytest
from fastapi.testclient import TestClient
from google.protobuf import struct_pb2

from app.coreclient import stubs
from app.coreclient.gen.dop.v1 import common_pb2, demand_pb2, knowledge_pb2
from app.coreclient.resolver import CoreResolver
from app.grpcapi.gen.dop.bff.v1 import knowledge_pb2 as bff
from app.grpcapi.gen.dop.bff.v1 import knowledge_pb2_grpc as bff_grpc
from app.grpcapi.interceptors import (
    AuthInterceptor,
    ErrorInterceptor,
    LoggingInterceptor,
)
from app.grpcapi.knowledge import KnowledgeServicer
from app.main import create_app
from app.platform.security.firebase import FirebaseVerifier
from app.routers import knowledge as rotas
from tests.conftest import PROJECT, FakeCall, metadata_for, token_for

ACCOUNT = metadata_for(token_for())
REST_HEADERS = {"authorization": token_for(), "x-account-id": "acct-1"}

CORPO_DA_REGRA = "nunca mergear desenv na feature"


def _struct(d: dict) -> struct_pb2.Struct:
    s = struct_pb2.Struct()
    s.update(d)
    return s


# ── núcleo fake ────────────────────────────────────────────────────────────


class ConhecimentoFalso:
    """Núcleo fake de conhecimento.

    Reproduz duas formas do núcleo de verdade que a borda existe para desfazer:
    o body do artefato pequeno viaja no `meta` sob a key interna `dop.body`,
    e os scores da busca vêm numa lista PARALELA à dos artefatos.
    """

    def __init__(self):
        self.regra = knowledge_pb2.KnowledgeArtifact(
            id="art-1",
            project=common_pb2.ProjectRef(id="prj-1"),
            kind=knowledge_pb2.KnowledgeArtifact.KIND_RULE,
            name="no-green-no-pr",
            version=2,
            meta=_struct(
                {"dop.body": CORPO_DA_REGRA, "dop.scope": "project", "autor": "ed"}
            ),
        )
        # Artefato GRANDE: mora no storage, então não tem body inline.
        self.indice = knowledge_pb2.KnowledgeArtifact(
            id="art-2",
            project=common_pb2.ProjectRef(id="prj-1"),
            kind=knowledge_pb2.KnowledgeArtifact.KIND_INDEX,
            name="dop-api",
            version=7,
            object_ref="dop-knowledge/acct-1/prj-1/index/dop-api",
            meta=_struct({"dop.scope": "project"}),
        )
        self.memoria = knowledge_pb2.KnowledgeArtifact(
            id="art-3",
            project=common_pb2.ProjectRef(id="prj-1"),
            kind=knowledge_pb2.KnowledgeArtifact.KIND_MEMORY,
            name="forense-timeout-2026-07",
            version=1,
            meta=_struct({"dop.body": "o timeout era do proxy", "dop.scope": "project"}),
        )
        self.pacote = knowledge_pb2.ContextPackage(
            demand=common_pb2.DemandRef(id="dem-1"),
            rules=[CORPO_DA_REGRA],
            index=[self.indice],
            memories=[self.memoria],
            findings=[
                demand_pb2.Finding(
                    id="f-1",
                    thread_id="th-1",
                    title="o proxy derruba conexão ociosa",
                    payload=_struct({"summary": "30s"}),
                )
            ],
            estimated_tokens=12_345,
            # O núcleo preenche SEMPRE o mapa, inclusive com zeros: "nada
            # descartado" e "não sei dizer" são fatos diferentes, e é o mapa
            # vazio que significa o segundo.
            dropped={"rules": 0, "findings": 2, "index": 1, "memories": 5},
        )
        self.BuildContextPackage = FakeCall(self.pacote)
        # Listas PARALELAS, como o núcleo returns.
        self.SearchMemory = FakeCall(
            knowledge_pb2.SearchMemoryResponse(
                artifacts=[self.memoria], scores=[0.8203125]
            )
        )
        self.ReadIndex = FakeCall(self.indice)
        self.PutArtifact = FakeCall(self.regra)
        self.ListRules = FakeCall(
            knowledge_pb2.ListRulesResponse(rules=[CORPO_DA_REGRA, "sem PR sem verde"])
        )


def pacote_com_descarte(base, **contagens) -> knowledge_pb2.ContextPackage:
    """O mesmo pacote, com outro mapa de descarte.

    Existe para que cada teste declare o descarte que está exercitando sem
    montar um `ContextPackage` inteiro — e para que o caso "o núcleo não
    informou" seja um mapa VAZIO de verdade, e não um double que imita presença
    de campo. O double que morava aqui (`PacoteComDescarte`) fingia o campo pelo
    descritor e por `HasField`; o campo chegou como MAPA, que não tem presença,
    e a imitação escondia que o caminho de produção levantaria `ValueError`.
    """
    p = knowledge_pb2.ContextPackage()
    p.CopyFrom(base)
    p.ClearField("dropped")
    p.dropped.update(contagens)
    return p


# ── fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def conhecimento(core, monkeypatch):
    fake = ConhecimentoFalso()
    monkeypatch.setattr(stubs, "knowledge_stub", lambda: fake)
    return fake


def _app_com_rotas():
    """O app real do BFF, com as rotas deste domínio registradas.

    `app/main.py` é do dono do repositório e ainda não inclui este router (ver o
    relatório). Registrar aqui mantém o teste honesto — ele exercita o app de
    verdade, com os middlewares de verdade — sem tocar em arquivo alheio. A
    checagem antes de incluir faz o teste continuar correto depois que o
    registro entrar no `main`.
    """
    app = create_app()
    caminhos = {getattr(r, "path", "") for r in app.routes}
    if "/api/v1/knowledge/rules" not in caminhos:
        app.include_router(rotas.router)
    return app


@pytest.fixture
def client_know(conhecimento):
    with TestClient(_app_com_rotas()) as c:
        yield c


@pytest.fixture
async def stub_know(conhecimento):
    """Servidor gRPC real, em porta efêmera, com o servicer deste domínio.

    Não reusa a fixture `grpc_server` do conftest porque `GrpcServer` ainda
    não registra este servicer (o registro é do dono do repositório). A pilha
    de interceptores é a MESMA, que é o que importa: é ela que faz o token, o
    context e os decorators valerem dentro do servicer.
    """
    server = grpc.aio.server(
        interceptors=(
            LoggingInterceptor(),
            ErrorInterceptor(),
            AuthInterceptor(FirebaseVerifier(PROJECT), CoreResolver()),
        )
    )
    bff_grpc.add_KnowledgeServiceServicer_to_server(KnowledgeServicer(), server)
    porta = server.add_insecure_port("127.0.0.1:0")
    await server.start()
    try:
        async with grpc.aio.insecure_channel(f"127.0.0.1:{porta}") as channel:
            yield bff_grpc.KnowledgeServiceStub(channel)
    finally:
        await server.stop(0)


# ── testes ──────────────────────────────────────────────────────────────────


class TestContextDrops:
    """O que não coube tem de aparecer. Ausente ≠ zerado."""

    def test_the_drops_come_from_the_cores_field(self, client_know):
        """`ContextPackage.dropped` existe (P-19) e a borda o LÊ.

        Antes a borda procurava o campo no DESCRITOR do protobuf, porque ele
        não estava no contrato. O contorno apostava que ele chegaria como
        mensagem; chegou como `map<string,int32>`, que não tem presença — e o
        `HasField` do contorno passou a levantar `ValueError` no primeiro
        pacote request. Este teste é o caminho novo, sem descritor no meio.
        """
        r = client_know.get(
            "/api/v1/demands/dem-1/context-package", headers=REST_HEADERS
        )
        assert r.status_code == 200
        assert r.json()["dropped"] == {
            "rules": 0,
            "findings": 2,
            "index": 1,
            "memories": 5,
            "truncated": True,
        }
        assert r.json()["estimated_tokens"] == 12_345

    def test_an_empty_map_is_null_not_zeroes(self, client_know, conhecimento):
        """`null` é "não dá para saber"; zeros seriam "nada foi descartado".

        O núcleo preenche o mapa sempre, com as quatro keys. Mapa vazio é o
        núcleo anterior ao campo — e preencher com zeros faria a screen afirmar
        que o context coube inteiro, que é justamente a mentira que a ADR-0012
        quer impedir.
        """
        conhecimento.BuildContextPackage.returns(
            pacote_com_descarte(conhecimento.pacote)
        )
        assert (
            client_know.get(
                "/api/v1/demands/dem-1/context-package", headers=REST_HEADERS
            ).json()["dropped"]
            is None
        )

    def test_zeroed_drops_are_not_truncated(self, client_know, conhecimento):
        """Zerado é AFIRMAÇÃO: coube tudo. Diferente de mapa vazio."""
        conhecimento.BuildContextPackage.returns(
            pacote_com_descarte(
                conhecimento.pacote, rules=0, findings=0, index=0, memories=0
            )
        )
        descarte = client_know.get(
            "/api/v1/demands/dem-1/context-package", headers=REST_HEADERS
        ).json()["dropped"]
        assert descarte["truncated"] is False
        assert descarte["findings"] == 0

    def test_a_layer_the_edge_does_not_know_still_truncates(
        self, client_know, conhecimento
    ):
        """Camada nova no núcleo não tem campo aqui — mas trunca do mesmo jeito.

        O número dela se perde (a borda só publica as quatro camadas da
        ADR-0009 §1), e isso é aceitável. O que NÃO é aceitável é a screen dizer
        "coube tudo" por causa de uma key que a borda não sabia ler.
        """
        conhecimento.BuildContextPackage.returns(
            pacote_com_descarte(
                conhecimento.pacote,
                rules=0,
                findings=0,
                index=0,
                memories=0,
                diagramas=3,
            )
        )
        descarte = client_know.get(
            "/api/v1/demands/dem-1/context-package", headers=REST_HEADERS
        ).json()["dropped"]
        assert descarte["truncated"] is True
        assert "diagramas" not in descarte

    async def test_absent_drops_over_grpc_too(self, stub_know, conhecimento):
        conhecimento.BuildContextPackage.returns(
            pacote_com_descarte(conhecimento.pacote)
        )
        resp = await stub_know.GetContextPackage(
            bff.GetContextPackageRequest(demand_id="dem-1"), metadata=ACCOUNT
        )
        assert not resp.HasField("dropped")

    async def test_drops_present_over_grpc(self, stub_know):
        resp = await stub_know.GetContextPackage(
            bff.GetContextPackageRequest(demand_id="dem-1"), metadata=ACCOUNT
        )
        assert resp.HasField("dropped")
        assert resp.dropped.findings == 2
        assert resp.dropped.truncated is True


class TestTheInternalConventionDoesNotLeak:
    """As keys `dop.*` do núcleo não atravessam para a screen.

    Procuradas no body serializado INTEIRO: conferir campo a campo só pegaria
    o vazamento que alguém lembrou de imaginar — e o próximo campo que carregar
    meta não estaria na lista.
    """

    def test_rest_promotes_body_and_scope_to_fields(self, client_know):
        r = client_know.get(
            "/api/v1/knowledge/index?project_id=prj-1&repo=dop-api",
            headers=REST_HEADERS,
        )
        assert r.status_code == 200
        assert "dop.body" not in r.text
        assert "dop.scope" not in r.text
        assert r.json()["scope"] == "project"
        assert r.json()["object_ref"].endswith("index/dop-api")
        # Artefato grande não tem body inline — ele mora no storage.
        assert r.json()["body"] == ""

    def test_rest_keeps_the_authors_meta(self, client_know, conhecimento):
        conhecimento.ReadIndex.returns(conhecimento.regra)
        artefato = client_know.get(
            "/api/v1/knowledge/index?project_id=prj-1&repo=x", headers=REST_HEADERS
        ).json()
        assert artefato["meta"] == {"autor": "ed"}
        assert artefato["body"] == CORPO_DA_REGRA

    async def test_grpc_does_not_carry_the_convention_either(self, stub_know):
        resp = await stub_know.ReadIndex(
            bff.ReadIndexRequest(project_id="prj-1", repo="dop-api"), metadata=ACCOUNT
        )
        assert b"dop.scope" not in resp.SerializeToString()
        assert resp.scope == "project"


class TestREST:
    def test_the_context_package(self, client_know):
        p = client_know.get(
            "/api/v1/demands/dem-1/context-package", headers=REST_HEADERS
        ).json()
        assert p["demand_id"] == "dem-1"
        assert p["rules"] == [CORPO_DA_REGRA]
        assert p["memories"][0]["kind"] == "memory"
        assert p["findings"][0]["payload"] == {"summary": "30s"}

    def test_the_search_pairs_artifact_and_score(self, client_know):
        hits = client_know.get(
            "/api/v1/knowledge/memory?q=timeout&project_id=prj-1",
            headers=REST_HEADERS,
        ).json()
        assert hits[0]["artifact"]["id"] == "art-3"
        assert hits[0]["score"] == pytest.approx(0.8203125)

    def test_an_absent_score_is_null_not_zero(self, client_know, conhecimento):
        """Score que o núcleo não mandou não vira "nenhuma semelhança"."""
        conhecimento.SearchMemory.returns(
            knowledge_pb2.SearchMemoryResponse(
                artifacts=[conhecimento.memoria], scores=[]
            )
        )
        hits = client_know.get(
            "/api/v1/knowledge/memory?q=timeout", headers=REST_HEADERS
        ).json()
        assert hits[0]["score"] is None

    def test_a_search_with_no_project_goes_to_the_account_scope(self, client_know, conhecimento):
        client_know.get("/api/v1/knowledge/memory?q=x", headers=REST_HEADERS)
        # Sem project o campo não é preenchido — o núcleo entende isso como
        # memória de account. Um ProjectRef vazio seria um project de id "".
        assert not conhecimento.SearchMemory.requests[0].HasField("project")

    def test_listing_rules(self, client_know):
        regras = client_know.get(
            "/api/v1/knowledge/rules?project_id=prj-1", headers=REST_HEADERS
        ).json()
        assert regras == [CORPO_DA_REGRA, "sem PR sem verde"]

    def test_writing_carries_an_idempotency_key(self, client_know, conhecimento):
        """Sem key, o retry do channel vira versão nova em silêncio."""
        r = client_know.post(
            "/api/v1/knowledge/artifacts",
            headers=REST_HEADERS,
            json={
                "kind": "rule",
                "name": "no-green-no-pr",
                "project_id": "prj-1",
                "content_base64": base64.b64encode(CORPO_DA_REGRA.encode()).decode(),
            },
        )
        assert r.status_code == 201
        request = conhecimento.PutArtifact.requests[0]
        assert request.idempotency_key != ""
        assert request.content == CORPO_DA_REGRA.encode()
        assert request.artifact.kind == knowledge_pb2.KnowledgeArtifact.KIND_RULE

    def test_the_knowledge_kind_is_validated_at_the_edge(self, client_know):
        """`kind` só aceita rule, index ou memory — 422 antes da ida ao núcleo."""
        r = client_know.post(
            "/api/v1/knowledge/artifacts",
            headers=REST_HEADERS,
            json={"kind": "anotacao", "name": "x", "content_base64": "eA=="},
        )
        assert r.status_code == 422

    def test_invalid_base64_is_a_422_not_a_500(self, client_know):
        r = client_know.post(
            "/api/v1/knowledge/artifacts",
            headers=REST_HEADERS,
            json={"kind": "memory", "name": "x", "content_base64": "não é base64!"},
        )
        assert r.status_code == 422

    def test_with_no_active_account_it_is_refused(self, client_know):
        """Regra do SP-0, aplicada pelo decorator no CASO DE USO."""
        r = client_know.get(
            "/api/v1/knowledge/rules?project_id=prj-1",
            headers={"authorization": token_for()},
        )
        assert r.status_code == 400


class TestGRPC:
    async def test_the_context_package(self, stub_know):
        resp = await stub_know.GetContextPackage(
            bff.GetContextPackageRequest(demand_id="dem-1"), metadata=ACCOUNT
        )
        assert resp.estimated_tokens == 12_345
        assert resp.memories[0].kind == bff.KNOWLEDGE_KIND_MEMORY

    async def test_the_search_is_paired(self, stub_know):
        resp = await stub_know.SearchMemory(
            bff.SearchMemoryRequest(query="timeout", project_id="prj-1"), metadata=ACCOUNT
        )
        assert resp.hits[0].artifact.id == "art-3"
        assert resp.hits[0].HasField("score")

    async def test_an_absent_score_stays_absent(self, stub_know, conhecimento):
        conhecimento.SearchMemory.returns(
            knowledge_pb2.SearchMemoryResponse(
                artifacts=[conhecimento.memoria], scores=[]
            )
        )
        resp = await stub_know.SearchMemory(
            bff.SearchMemoryRequest(query="x"), metadata=ACCOUNT
        )
        assert not resp.hits[0].HasField("score")

    async def test_the_client_sends_its_own_idempotency_key(self, stub_know, conhecimento):
        await stub_know.PutArtifact(
            bff.PutArtifactRequest(
                kind=bff.KNOWLEDGE_KIND_MEMORY,
                name="lição",
                project_id="prj-1",
                content=b"o timeout era do proxy",
                idempotency_key="key-do-client",
            ),
            metadata=ACCOUNT,
        )
        assert conhecimento.PutArtifact.requests[0].idempotency_key == "key-do-client"

    async def test_with_no_token(self, stub_know):
        with pytest.raises(grpc.aio.AioRpcError) as e:
            await stub_know.ListRules(
                bff.ListRulesRequest(project_id="prj-1"), metadata=metadata_for(None)
            )
        assert e.value.code() == grpc.StatusCode.UNAUTHENTICATED

    async def test_an_invalid_kind_is_invalid_argument(self, stub_know, conhecimento):
        with pytest.raises(grpc.aio.AioRpcError) as e:
            await stub_know.PutArtifact(
                bff.PutArtifactRequest(name="x", content=b"y"), metadata=ACCOUNT
            )
        assert e.value.code() == grpc.StatusCode.INVALID_ARGUMENT
        assert conhecimento.PutArtifact.calls == []


class TestParityBetweenTransports:
    """O alarme que dispara se alguém reimplementar o caso de uso num adaptador."""

    async def test_the_package_is_the_same_on_both_ports(self, client_know, stub_know):
        rest = client_know.get(
            "/api/v1/demands/dem-1/context-package", headers=REST_HEADERS
        ).json()
        resp = await stub_know.GetContextPackage(
            bff.GetContextPackageRequest(demand_id="dem-1"), metadata=ACCOUNT
        )
        assert resp.demand_id == rest["demand_id"]
        assert list(resp.rules) == rest["rules"]
        assert resp.estimated_tokens == rest["estimated_tokens"]
        # Ausência do descarte é a MESMA nas duas portas: null no JSON, campo
        # não preenchido no protobuf.
        assert resp.HasField("dropped") is (rest["dropped"] is not None)
        for g, j in zip(resp.memories, rest["memories"], strict=True):
            assert g.id == j["id"]
            assert g.name == j["name"]
            assert g.body == j["body"]
            assert g.scope == j["scope"]
