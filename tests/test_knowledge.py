"""Conhecimento nos dois transportes, contra um núcleo falso.

Dois testes carregam o arquivo:

  - o de DESCARTE: o pacote de contexto é selecionado por orçamento (ADR-0012),
    e a borda tem de conseguir dizer "não coube tudo". Hoje o núcleo não informa
    o descarte, e a resposta honesta é `null` — não zeros, que AFIRMARIAM que
    nada ficou de fora;
  - o de vazamento de convenção interna: as chaves `dop.body`/`dop.scope` do
    núcleo não podem atravessar para a tela. Ele é escrito procurando a chave no
    corpo serializado INTEIRO, e não campo por campo — do jeito que
    `tests/test_resource.py` procura o segredo —, porque conferir campo a campo
    só pega o vazamento que alguém lembrou de imaginar.

Fakes e fixtures ficam AQUI, e não no `conftest.py`: há outros agentes
escrevendo neste repositório agora.
"""

import base64
from types import SimpleNamespace

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
from tests.conftest import PROJECT, ChamadaFalsa, metadados_de, token_de

CONTA = metadados_de(token_de())
CABECALHOS_REST = {"authorization": token_de(), "x-account-id": "acct-1"}

CORPO_DA_REGRA = "nunca mergear desenv na feature"


def _struct(d: dict) -> struct_pb2.Struct:
    s = struct_pb2.Struct()
    s.update(d)
    return s


# ── núcleo falso ────────────────────────────────────────────────────────────


class ConhecimentoFalso:
    """Núcleo falso de conhecimento.

    Reproduz duas formas do núcleo de verdade que a borda existe para desfazer:
    o corpo do artefato pequeno viaja no `meta` sob a chave interna `dop.body`,
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
        # Artefato GRANDE: mora no storage, então não tem corpo inline.
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
        )
        self.BuildContextPackage = ChamadaFalsa(self.pacote)
        # Listas PARALELAS, como o núcleo devolve.
        self.SearchMemory = ChamadaFalsa(
            knowledge_pb2.SearchMemoryResponse(
                artifacts=[self.memoria], scores=[0.8203125]
            )
        )
        self.ReadIndex = ChamadaFalsa(self.indice)
        self.PutArtifact = ChamadaFalsa(self.regra)
        self.ListRules = ChamadaFalsa(
            knowledge_pb2.ListRulesResponse(rules=[CORPO_DA_REGRA, "sem PR sem verde"])
        )


class PacoteComDescarte:
    """O `ContextPackage` do núcleo NO DIA em que ele publicar `dropped`.

    Existe porque o campo ainda não está em `dop.v1` (o núcleo calcula o
    descarte e o grava como métrica, mas não o devolve). A borda faz a detecção
    pelo DESCRITOR justamente para passar a mostrá-lo sem mudança de código —
    e este duplo é o que prova que a detecção funciona, em vez de deixá-la como
    promessa não exercida.
    """

    def __init__(self, base, **contagens):
        self._base = base
        self.dropped = SimpleNamespace(**contagens)
        self.DESCRIPTOR = SimpleNamespace(
            fields_by_name={**base.DESCRIPTOR.fields_by_name, "dropped": object()}
        )

    def __getattr__(self, nome):
        return getattr(self._base, nome)

    def HasField(self, nome: str) -> bool:
        return True if nome == "dropped" else self._base.HasField(nome)


# ── fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def conhecimento(nucleo, monkeypatch):
    falso = ConhecimentoFalso()
    monkeypatch.setattr(stubs, "knowledge_stub", lambda: falso)
    return falso


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
def cliente_know(conhecimento):
    with TestClient(_app_com_rotas()) as c:
        yield c


@pytest.fixture
async def stub_know(conhecimento):
    """Servidor gRPC real, em porta efêmera, com o servicer deste domínio.

    Não reusa a fixture `servidor_grpc` do conftest porque `GrpcServer` ainda
    não registra este servicer (o registro é do dono do repositório). A pilha
    de interceptores é a MESMA, que é o que importa: é ela que faz o token, o
    contexto e os decorators valerem dentro do servicer.
    """
    servidor = grpc.aio.server(
        interceptors=(
            LoggingInterceptor(),
            ErrorInterceptor(),
            AuthInterceptor(FirebaseVerifier(PROJECT), CoreResolver()),
        )
    )
    bff_grpc.add_KnowledgeServiceServicer_to_server(KnowledgeServicer(), servidor)
    porta = servidor.add_insecure_port("127.0.0.1:0")
    await servidor.start()
    try:
        async with grpc.aio.insecure_channel(f"127.0.0.1:{porta}") as canal:
            yield bff_grpc.KnowledgeServiceStub(canal)
    finally:
        await servidor.stop(0)


# ── testes ──────────────────────────────────────────────────────────────────


class TestDescarteDeContexto:
    """O que não coube tem de aparecer. Ausente ≠ zerado."""

    def test_sem_informacao_do_nucleo_o_descarte_vem_nulo(self, cliente_know):
        """`null` é "não dá para saber"; zero seria "nada foi descartado".

        Preencher com zeros faria a tela afirmar que o contexto coube inteiro —
        exatamente a mentira que a ADR-0012 quer impedir.
        """
        r = cliente_know.get(
            "/api/v1/demands/dem-1/context-package", headers=CABECALHOS_REST
        )
        assert r.status_code == 200
        assert r.json()["dropped"] is None
        assert r.json()["estimated_tokens"] == 12_345

    def test_quando_o_nucleo_informar_a_borda_mostra(self, cliente_know, conhecimento):
        conhecimento.BuildContextPackage.devolve(
            PacoteComDescarte(
                conhecimento.pacote, rules=0, findings=2, index=1, memories=5
            )
        )
        descarte = cliente_know.get(
            "/api/v1/demands/dem-1/context-package", headers=CABECALHOS_REST
        ).json()["dropped"]
        assert descarte == {
            "rules": 0,
            "findings": 2,
            "index": 1,
            "memories": 5,
            "truncated": True,
        }

    def test_descarte_zerado_nao_e_truncado(self, cliente_know, conhecimento):
        conhecimento.BuildContextPackage.devolve(
            PacoteComDescarte(
                conhecimento.pacote, rules=0, findings=0, index=0, memories=0
            )
        )
        descarte = cliente_know.get(
            "/api/v1/demands/dem-1/context-package", headers=CABECALHOS_REST
        ).json()["dropped"]
        assert descarte["truncated"] is False

    async def test_descarte_ausente_tambem_no_grpc(self, stub_know):
        resp = await stub_know.GetContextPackage(
            bff.GetContextPackageRequest(demand_id="dem-1"), metadata=CONTA
        )
        assert not resp.HasField("dropped")


class TestConvencaoInternaNaoVaza:
    """As chaves `dop.*` do núcleo não atravessam para a tela.

    Procuradas no corpo serializado INTEIRO: conferir campo a campo só pegaria
    o vazamento que alguém lembrou de imaginar — e o próximo campo que carregar
    meta não estaria na lista.
    """

    def test_rest_promove_body_e_scope_a_campos(self, cliente_know):
        r = cliente_know.get(
            "/api/v1/knowledge/index?project_id=prj-1&repo=dop-api",
            headers=CABECALHOS_REST,
        )
        assert r.status_code == 200
        assert "dop.body" not in r.text
        assert "dop.scope" not in r.text
        assert r.json()["scope"] == "project"
        assert r.json()["object_ref"].endswith("index/dop-api")
        # Artefato grande não tem corpo inline — ele mora no storage.
        assert r.json()["body"] == ""

    def test_rest_mantem_o_meta_do_autor(self, cliente_know, conhecimento):
        conhecimento.ReadIndex.devolve(conhecimento.regra)
        artefato = cliente_know.get(
            "/api/v1/knowledge/index?project_id=prj-1&repo=x", headers=CABECALHOS_REST
        ).json()
        assert artefato["meta"] == {"autor": "ed"}
        assert artefato["body"] == CORPO_DA_REGRA

    async def test_grpc_tambem_nao_carrega_a_convencao(self, stub_know):
        resp = await stub_know.ReadIndex(
            bff.ReadIndexRequest(project_id="prj-1", repo="dop-api"), metadata=CONTA
        )
        assert b"dop.scope" not in resp.SerializeToString()
        assert resp.scope == "project"


class TestREST:
    def test_pacote_de_contexto(self, cliente_know):
        p = cliente_know.get(
            "/api/v1/demands/dem-1/context-package", headers=CABECALHOS_REST
        ).json()
        assert p["demand_id"] == "dem-1"
        assert p["rules"] == [CORPO_DA_REGRA]
        assert p["memories"][0]["kind"] == "memory"
        assert p["findings"][0]["payload"] == {"summary": "30s"}

    def test_busca_pareia_artefato_e_score(self, cliente_know):
        hits = cliente_know.get(
            "/api/v1/knowledge/memory?q=timeout&project_id=prj-1",
            headers=CABECALHOS_REST,
        ).json()
        assert hits[0]["artifact"]["id"] == "art-3"
        assert hits[0]["score"] == pytest.approx(0.8203125)

    def test_score_ausente_e_nulo_nao_zero(self, cliente_know, conhecimento):
        """Score que o núcleo não mandou não vira "nenhuma semelhança"."""
        conhecimento.SearchMemory.devolve(
            knowledge_pb2.SearchMemoryResponse(
                artifacts=[conhecimento.memoria], scores=[]
            )
        )
        hits = cliente_know.get(
            "/api/v1/knowledge/memory?q=timeout", headers=CABECALHOS_REST
        ).json()
        assert hits[0]["score"] is None

    def test_busca_sem_projeto_vai_ao_escopo_de_conta(self, cliente_know, conhecimento):
        cliente_know.get("/api/v1/knowledge/memory?q=x", headers=CABECALHOS_REST)
        # Sem projeto o campo não é preenchido — o núcleo entende isso como
        # memória de conta. Um ProjectRef vazio seria um projeto de id "".
        assert not conhecimento.SearchMemory.pedidos[0].HasField("project")

    def test_listar_regras(self, cliente_know):
        regras = cliente_know.get(
            "/api/v1/knowledge/rules?project_id=prj-1", headers=CABECALHOS_REST
        ).json()
        assert regras == [CORPO_DA_REGRA, "sem PR sem verde"]

    def test_gravar_carrega_idempotencia(self, cliente_know, conhecimento):
        """Sem chave, o retry do canal vira versão nova em silêncio."""
        r = cliente_know.post(
            "/api/v1/knowledge/artifacts",
            headers=CABECALHOS_REST,
            json={
                "kind": "rule",
                "name": "no-green-no-pr",
                "project_id": "prj-1",
                "content_base64": base64.b64encode(CORPO_DA_REGRA.encode()).decode(),
            },
        )
        assert r.status_code == 201
        pedido = conhecimento.PutArtifact.pedidos[0]
        assert pedido.idempotency_key != ""
        assert pedido.content == CORPO_DA_REGRA.encode()
        assert pedido.artifact.kind == knowledge_pb2.KnowledgeArtifact.KIND_RULE

    def test_tipo_de_conhecimento_e_validado_na_borda(self, cliente_know):
        """`kind` só aceita rule, index ou memory — 422 antes da ida ao núcleo."""
        r = cliente_know.post(
            "/api/v1/knowledge/artifacts",
            headers=CABECALHOS_REST,
            json={"kind": "anotacao", "name": "x", "content_base64": "eA=="},
        )
        assert r.status_code == 422

    def test_base64_invalido_e_422_nao_500(self, cliente_know):
        r = cliente_know.post(
            "/api/v1/knowledge/artifacts",
            headers=CABECALHOS_REST,
            json={"kind": "memory", "name": "x", "content_base64": "não é base64!"},
        )
        assert r.status_code == 422

    def test_sem_conta_ativa_e_recusado(self, cliente_know):
        """Regra do SP-0, aplicada pelo decorator no CASO DE USO."""
        r = cliente_know.get(
            "/api/v1/knowledge/rules?project_id=prj-1",
            headers={"authorization": token_de()},
        )
        assert r.status_code == 400


class TestGRPC:
    async def test_pacote_de_contexto(self, stub_know):
        resp = await stub_know.GetContextPackage(
            bff.GetContextPackageRequest(demand_id="dem-1"), metadata=CONTA
        )
        assert resp.estimated_tokens == 12_345
        assert resp.memories[0].kind == bff.KNOWLEDGE_KIND_MEMORY

    async def test_busca_pareada(self, stub_know):
        resp = await stub_know.SearchMemory(
            bff.SearchMemoryRequest(query="timeout", project_id="prj-1"), metadata=CONTA
        )
        assert resp.hits[0].artifact.id == "art-3"
        assert resp.hits[0].HasField("score")

    async def test_score_ausente_fica_ausente(self, stub_know, conhecimento):
        conhecimento.SearchMemory.devolve(
            knowledge_pb2.SearchMemoryResponse(
                artifacts=[conhecimento.memoria], scores=[]
            )
        )
        resp = await stub_know.SearchMemory(
            bff.SearchMemoryRequest(query="x"), metadata=CONTA
        )
        assert not resp.hits[0].HasField("score")

    async def test_cliente_manda_a_propria_idempotencia(self, stub_know, conhecimento):
        await stub_know.PutArtifact(
            bff.PutArtifactRequest(
                kind=bff.KNOWLEDGE_KIND_MEMORY,
                name="lição",
                project_id="prj-1",
                content=b"o timeout era do proxy",
                idempotency_key="chave-do-cliente",
            ),
            metadata=CONTA,
        )
        assert conhecimento.PutArtifact.pedidos[0].idempotency_key == "chave-do-cliente"

    async def test_sem_token(self, stub_know):
        with pytest.raises(grpc.aio.AioRpcError) as e:
            await stub_know.ListRules(
                bff.ListRulesRequest(project_id="prj-1"), metadata=metadados_de(None)
            )
        assert e.value.code() == grpc.StatusCode.UNAUTHENTICATED

    async def test_tipo_invalido_e_invalid_argument(self, stub_know, conhecimento):
        with pytest.raises(grpc.aio.AioRpcError) as e:
            await stub_know.PutArtifact(
                bff.PutArtifactRequest(name="x", content=b"y"), metadata=CONTA
            )
        assert e.value.code() == grpc.StatusCode.INVALID_ARGUMENT
        assert conhecimento.PutArtifact.chamadas == []


class TestParidadeEntreTransportes:
    """O alarme que dispara se alguém reimplementar o caso de uso num adaptador."""

    async def test_pacote_igual_nas_duas_portas(self, cliente_know, stub_know):
        rest = cliente_know.get(
            "/api/v1/demands/dem-1/context-package", headers=CABECALHOS_REST
        ).json()
        resp = await stub_know.GetContextPackage(
            bff.GetContextPackageRequest(demand_id="dem-1"), metadata=CONTA
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
