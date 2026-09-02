"""Knowledge on both transports, against a fake core.

Two tests carry this file:

  - the DROPS one: the context package is selected by a budget (ADR-0012), and
    the edge has to be able to say "not everything fitted". The core reports the
    drops in a `map<string,int32>` per layer, and always fills it in — an EMPTY
    map is a silent core, and becomes `null`, not zeroes, which would ASSERT
    nothing was left out;
  - the internal-convention leak one: the core's `dop.body`/`dop.scope` keys
    must not cross to the screen. It is written by looking for the key in the
    WHOLE serialized body, and not field by field — the way
    `tests/test_resource.py` looks for the secret — because checking field by
    field only catches the leak somebody remembered to imagine.

The fakes and the fixtures live HERE, and not in `conftest.py`: there are other
agents writing in this repository right now.
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
    """Núcleo fake de knowledge.

    It reproduces two shapes of the real core that the edge exists to undo:
    o body do artefato pequeno viaja no `meta` sob a key interna `dop.body`,
    and the search's scores come in a list PARALLEL to the artifacts'.
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
        # A LARGE artifact: it lives in storage, so it has no inline body.
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
        self.package = knowledge_pb2.ContextPackage(
            demand=common_pb2.DemandRef(id="dem-1"),
            rules=[CORPO_DA_REGRA],
            index=[self.indice],
            memories=[self.memoria],
            findings=[
                demand_pb2.Finding(
                    id="f-1",
                    thread_id="th-1",
                    title="the proxy drops an idle connection",
                    payload=_struct({"summary": "30s"}),
                )
            ],
            estimated_tokens=12_345,
            # The core ALWAYS fills the map in, zeroes included: "nothing was
            # dropped" and "I cannot tell" are different facts, and it is the
            # empty map that means the second.
            dropped={"rules": 0, "findings": 2, "index": 1, "memories": 5},
        )
        self.BuildContextPackage = FakeCall(self.package)
        # PARALLEL lists, as the core returns them.
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


def package_with_drops(base, **contagens) -> knowledge_pb2.ContextPackage:
    """O mesmo package, com outro mapa de drops.

    It exists so each test declares the drops it is exercising without building
    a whole `ContextPackage` — and so the "the core did not report" case is a
    genuinely EMPTY map, and not a double that imitates field presence. The
    double that used to live here (`PacoteComDescarte`) faked the field through
    the descriptor and `HasField`; the field arrived as a MAP, which has no
    presence, and the imitation hid that the production path would raise a
    `ValueError`.
    """
    p = knowledge_pb2.ContextPackage()
    p.CopyFrom(base)
    p.ClearField("dropped")
    p.dropped.update(contagens)
    return p


# ── fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def knowledge(core, monkeypatch):
    fake = ConhecimentoFalso()
    monkeypatch.setattr(stubs, "knowledge_stub", lambda: fake)
    return fake


def _app_with_routes():
    """The BFF's real app, with this domain's routes registered.

    `app/main.py` belongs to the repository's owner and does not include this
    router yet (see the report). Registering it here keeps the test honest — it
    exercises the real app, with the real middlewares — without touching somebody
    else's file. The check before including makes the test stay correct once the
    registration lands in `main`.
    """
    app = create_app()
    paths = {getattr(r, "path", "") for r in app.routes}
    if "/api/v1/knowledge/rules" not in paths:
        app.include_router(rotas.router)
    return app


@pytest.fixture
def client_know(knowledge):
    with TestClient(_app_with_routes()) as c:
        yield c


@pytest.fixture
async def stub_know(knowledge):
    """A real gRPC server, on an ephemeral port, with this domain's servicer.

    It does not reuse conftest's `grpc_server` fixture because `GrpcServer` does
    not register this servicer yet (the registration is the repository owner's).
    The interceptor stack is the SAME, which is what matters: it is what makes
    the token, the context and the decorators hold inside the servicer.
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
    """What did not fit has to show up. Absent ≠ zeroed."""

    def test_the_drops_come_from_the_cores_field(self, client_know):
        """`ContextPackage.dropped` existe (P-19) e a borda o LÊ.

        The edge used to look for the field in protobuf's DESCRIPTOR, because it
        was not in the contract. The workaround bet it would arrive as a message;
        it arrived as a `map<string,int32>`, which has no presence — and the
        workaround's `HasField` started raising `ValueError` on the first package
        requested. This test is the new path, with no descriptor in between.
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

    def test_an_empty_map_is_null_not_zeroes(self, client_know, knowledge):
        """`null` is "there is no way to know"; zeroes would be "nothing was dropped".

        The core always fills the map in, with all four keys. An empty map is
        the
        núcleo anterior ao campo — e preencher com zeros faria a screen afirmar
        that the context fitted whole, which is precisely the lie ADR-0012
        quer impedir.
        """
        knowledge.BuildContextPackage.returns(
            package_with_drops(knowledge.package)
        )
        assert (
            client_know.get(
                "/api/v1/demands/dem-1/context-package", headers=REST_HEADERS
            ).json()["dropped"]
            is None
        )

    def test_zeroed_drops_are_not_truncated(self, client_know, knowledge):
        """Zeroed is an ASSERTION: everything fitted. Different from an empty map."""
        knowledge.BuildContextPackage.returns(
            package_with_drops(
                knowledge.package, rules=0, findings=0, index=0, memories=0
            )
        )
        drops = client_know.get(
            "/api/v1/demands/dem-1/context-package", headers=REST_HEADERS
        ).json()["dropped"]
        assert drops["truncated"] is False
        assert drops["findings"] == 0

    def test_a_layer_the_edge_does_not_know_still_truncates(
        self, client_know, knowledge
    ):
        """A new layer in the core has no field here — but it truncates all the same.

        Its number is lost (the edge only publishes ADR-0009 §1's four layers),
        and that is acceptable. What is NOT acceptable is the screen saying
        "everything fitted" because of a key the edge did not know how to read.
        """
        knowledge.BuildContextPackage.returns(
            package_with_drops(
                knowledge.package,
                rules=0,
                findings=0,
                index=0,
                memories=0,
                diagramas=3,
            )
        )
        drops = client_know.get(
            "/api/v1/demands/dem-1/context-package", headers=REST_HEADERS
        ).json()["dropped"]
        assert drops["truncated"] is True
        assert "diagramas" not in drops

    async def test_absent_drops_over_grpc_too(self, stub_know, knowledge):
        knowledge.BuildContextPackage.returns(
            package_with_drops(knowledge.package)
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
    """The core's `dop.*` keys do not cross to the screen.

    Looked for in the WHOLE serialized body: checking field by field would only
    catch the leak somebody remembered to imagine — and the next field to carry a
    meta would not be on the list.
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
        # A large artifact has no inline body — it lives in storage.
        assert r.json()["body"] == ""

    def test_rest_keeps_the_authors_meta(self, client_know, knowledge):
        knowledge.ReadIndex.returns(knowledge.regra)
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

    def test_an_absent_score_is_null_not_zero(self, client_know, knowledge):
        """A score the core did not send does not become "no similarity"."""
        knowledge.SearchMemory.returns(
            knowledge_pb2.SearchMemoryResponse(
                artifacts=[knowledge.memoria], scores=[]
            )
        )
        hits = client_know.get(
            "/api/v1/knowledge/memory?q=timeout", headers=REST_HEADERS
        ).json()
        assert hits[0]["score"] is None

    def test_a_search_with_no_project_goes_to_the_account_scope(self, client_know, knowledge):
        client_know.get("/api/v1/knowledge/memory?q=x", headers=REST_HEADERS)
        # With no project the field is not filled in — the core reads that as
        # memória de account. Um ProjectRef vazio seria um project de id "".
        assert not knowledge.SearchMemory.requests[0].HasField("project")

    def test_listing_rules(self, client_know):
        regras = client_know.get(
            "/api/v1/knowledge/rules?project_id=prj-1", headers=REST_HEADERS
        ).json()
        assert regras == [CORPO_DA_REGRA, "sem PR sem verde"]

    def test_writing_carries_an_idempotency_key(self, client_know, knowledge):
        """With no key, the channel's retry silently becomes a new version."""
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
        request = knowledge.PutArtifact.requests[0]
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
            json={"kind": "memory", "name": "x", "content_base64": "not base64!"},
        )
        assert r.status_code == 422

    def test_with_no_active_account_it_is_refused(self, client_know):
        """SP-0's rule, applied by the decorator in the USE CASE."""
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

    async def test_an_absent_score_stays_absent(self, stub_know, knowledge):
        knowledge.SearchMemory.returns(
            knowledge_pb2.SearchMemoryResponse(
                artifacts=[knowledge.memoria], scores=[]
            )
        )
        resp = await stub_know.SearchMemory(
            bff.SearchMemoryRequest(query="x"), metadata=ACCOUNT
        )
        assert not resp.hits[0].HasField("score")

    async def test_the_client_sends_its_own_idempotency_key(self, stub_know, knowledge):
        await stub_know.PutArtifact(
            bff.PutArtifactRequest(
                kind=bff.KNOWLEDGE_KIND_MEMORY,
                name="lesson",
                project_id="prj-1",
                content=b"o timeout era do proxy",
                idempotency_key="key-do-client",
            ),
            metadata=ACCOUNT,
        )
        assert knowledge.PutArtifact.requests[0].idempotency_key == "key-do-client"

    async def test_with_no_token(self, stub_know):
        with pytest.raises(grpc.aio.AioRpcError) as e:
            await stub_know.ListRules(
                bff.ListRulesRequest(project_id="prj-1"), metadata=metadata_for(None)
            )
        assert e.value.code() == grpc.StatusCode.UNAUTHENTICATED

    async def test_an_invalid_kind_is_invalid_argument(self, stub_know, knowledge):
        with pytest.raises(grpc.aio.AioRpcError) as e:
            await stub_know.PutArtifact(
                bff.PutArtifactRequest(name="x", content=b"y"), metadata=ACCOUNT
            )
        assert e.value.code() == grpc.StatusCode.INVALID_ARGUMENT
        assert knowledge.PutArtifact.calls == []


class TestParityBetweenTransports:
    """The alarm that fires if anybody reimplements the use case in an adapter."""

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
        # The absence of drops is the SAME on both ports: null in the JSON, an
        # unfilled field in the protobuf.
        assert resp.HasField("dropped") is (rest["dropped"] is not None)
        for g, j in zip(resp.memories, rest["memories"], strict=True):
            assert g.id == j["id"]
            assert g.name == j["name"]
            assert g.body == j["body"]
            assert g.scope == j["scope"]
