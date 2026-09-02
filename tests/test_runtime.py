"""A borda de execução de turno — e a invariante de que ela não tem segredo.

O runtime foi movido para o núcleo (ADR-0023). O que sobra aqui é tradução, e
os testes refletem isso: a suíte de contrato dos provedores vive no dop-core,
em `test/contract/agentprovider.go`, junto do código que ela exercita.

O último teste deste arquivo é o mais importante e não tem nada a ver com
tradução: ele guarda a invariante que motivou a mudança.
"""

import pathlib

import grpc
import pytest
from fastapi.testclient import TestClient

from app.coreclient import stubs
from app.coreclient.gen.dop.v1 import agent_pb2, common_pb2
from app.grpcapi.gen.dop.bff.v1 import runtime_pb2 as bff
from app.grpcapi.gen.dop.bff.v1 import runtime_pb2_grpc as bff_runtime_grpc
from app.main import create_app
from tests.conftest import FakeCall, metadata_for, token_for

ACCOUNT = metadata_for(token_for())
HEADERS = {"authorization": token_for(), "x-account-id": "acct-1"}


class AgenteFalso:
    """Núcleo fake do AgentService — o turno acontece LÁ, não aqui."""

    def __init__(self):
        self.RunTurn = FakeCall(
            agent_pb2.TurnOutcome(
                demand=common_pb2.DemandRef(id="dem-1"),
                thread_id="th-1",
                provider="anthropic",
                routing=agent_pb2.TurnRouting(
                    task_kind="investigation",
                    model="claude-sonnet",
                    effort="medium",
                    effort_applied="medium",
                    reason="ADR-0011 §3 (rascunho — calibrar com telemetria, P-7): forense",
                ),
                reply="A lentidão vem da falta de índice.",
                message_ids=["m-1", "m-2"],
                usage=agent_pb2.TurnUsage(
                    input_tokens=1000, output_tokens=800,
                    cache_read_tokens=500, cache_creation_tokens=0,
                    cache_creation_known=False, cost_known=True,
                ),
                context_truncated=True,
            )
        )


@pytest.fixture
def agente(core, monkeypatch):
    fake = AgenteFalso()
    monkeypatch.setattr(stubs, "agent_stub", lambda: fake)
    return fake


@pytest.fixture
def client_rt(agente):
    with TestClient(create_app()) as c:
        yield c


@pytest.fixture
async def stub_rt(grpc_server, agente):
    async with grpc.aio.insecure_channel(f"127.0.0.1:{grpc_server.port}") as channel:
        yield bff_runtime_grpc.RuntimeServiceStub(channel)


class TestTraducao:
    def test_turno_chega_ao_nucleo(self, client_rt, agente):
        r = client_rt.post(
            "/api/v1/demands/dem-1/threads/th-1/turns",
            headers={**HEADERS, "Idempotency-Key": "minha-key"},
            json={"text": "por que está lento?", "task_kind": "investigation"},
        )
        assert r.status_code == 200
        assert r.json()["reply"].startswith("A lentidão")
        assert agente.RunTurn.requests[0].demand_id == "dem-1"

    def test_a_borda_nao_inventa_chave_de_idempotencia(self, client_rt, agente):
        """Turno gasta dinheiro.

        Em toda outra escrita a borda gera a key, porque protege do retry do
        channel. Aqui NÃO: uma key inventada transformaria retry de rede em
        consumo em dobro. É o client quem sabe se está retentando.
        """
        client_rt.post(
            "/api/v1/demands/dem-1/threads/th-1/turns",
            headers=HEADERS,
            json={"text": "oi"},
        )
        assert agente.RunTurn.requests[0].idempotency_key == ""

    def test_contexto_truncado_atravessa(self, client_rt):
        r = client_rt.post(
            "/api/v1/demands/dem-1/threads/th-1/turns",
            headers=HEADERS, json={"text": "oi"},
        )
        assert r.json()["context_truncated"] is True

    def test_cache_desconhecido_nao_vira_zero_afirmado(self, client_rt):
        """Zero com `cache_creation_known=false` é "não sei", não "não houve"."""
        u = client_rt.post(
            "/api/v1/demands/dem-1/threads/th-1/turns",
            headers=HEADERS, json={"text": "oi"},
        ).json()["usage"]
        assert u["cache_creation_tokens"] == 0
        assert u["cache_creation_known"] is False

    def test_justificativa_do_roteamento_vai_inteira(self, client_rt):
        """É a única parte auditável da decisão (ADR-0011 §3)."""
        r = client_rt.post(
            "/api/v1/demands/dem-1/threads/th-1/turns",
            headers=HEADERS, json={"text": "oi"},
        )
        assert "ADR-0011 §3" in r.json()["routing"]["reason"]

    def test_prazo_do_turno_e_maior_que_o_normal(self, client_rt, agente):
        """Turno leva minutos; o prazo normal do núcleo derrubaria todos."""
        client_rt.post(
            "/api/v1/demands/dem-1/threads/th-1/turns",
            headers=HEADERS, json={"text": "oi"},
        )
        assert agente.RunTurn.calls[0]["timeout"] > 60


class TestGRPC:
    async def test_paridade_com_o_rest(self, client_rt, stub_rt):
        rest = client_rt.post(
            "/api/v1/demands/dem-1/threads/th-1/turns",
            headers=HEADERS, json={"text": "oi", "task_kind": "investigation"},
        ).json()
        g = await stub_rt.RunTurn(
            bff.RunTurnRequest(
                demand_id="dem-1", thread_id="th-1", text="oi",
                task_kind="investigation", idempotency_key="k",
            ),
            metadata=ACCOUNT,
        )
        assert g.reply == rest["reply"]
        assert g.routing.reason == rest["routing"]["reason"]
        assert g.context_truncated == rest["context_truncated"]

    async def test_sem_token(self, stub_rt):
        with pytest.raises(grpc.aio.AioRpcError) as e:
            await stub_rt.RunTurn(
                bff.RunTurnRequest(demand_id="d", thread_id="t", text="x"),
                metadata=metadata_for(None),
            )
        assert e.value.code() == grpc.StatusCode.UNAUTHENTICATED


class TestOBffNaoTemSegredo:
    """A invariante que motivou a ADR-0023, virada teste (P-22).

    Irmã de "o BFF não tem banco": esta camada é a exposta à internet, e
    comprometê-la não pode entregar credencial de coisa nenhuma. Disciplina não
    basta — alguém adiciona um `import` de boa-fé e ninguém percebe na revisão.
    """

    def test_nenhum_modulo_toca_cofre_ou_chave_de_provedor(self):
        proibidos = [
            "secretstore",
            "SecretStore",
            "ANTHROPIC_API_KEY",
            "OPENAI_API_KEY",
            "anthropic",
        ]
        app = pathlib.Path(__file__).resolve().parent.parent / "app"
        findings = []
        for py in app.rglob("*.py"):
            # Os stubs gerados citam names de serviço do núcleo; o que importa
            # é o código escrito à mão.
            if "/gen/" in str(py):
                continue
            text = py.read_text(encoding="utf-8")
            for termo in proibidos:
                if termo in text:
                    findings.append(f"{py.relative_to(app)}: {termo}")
        assert not findings, (
            "o BFF não pode ter segredo nem falar com provedor de modelo "
            f"(ADR-0023): {findings}"
        )

    def test_o_runtime_nao_voltou_para_ca(self):
        app = pathlib.Path(__file__).resolve().parent.parent / "app"
        assert not (app / "runtime").exists(), (
            "app/runtime/ ressuscitou: o runtime vive no NÚCLEO (ADR-0023), "
            "onde a credencial não atravessa a rede"
        )
