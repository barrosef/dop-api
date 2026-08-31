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
from tests.conftest import ChamadaFalsa, metadados_de, token_de

CONTA = metadados_de(token_de())
CABECALHOS = {"authorization": token_de(), "x-account-id": "acct-1"}


class AgenteFalso:
    """Núcleo falso do AgentService — o turno acontece LÁ, não aqui."""

    def __init__(self):
        self.RunTurn = ChamadaFalsa(
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
def agente(nucleo, monkeypatch):
    falso = AgenteFalso()
    monkeypatch.setattr(stubs, "agent_stub", lambda: falso)
    return falso


@pytest.fixture
def cliente_rt(agente):
    with TestClient(create_app()) as c:
        yield c


@pytest.fixture
async def stub_rt(servidor_grpc, agente):
    async with grpc.aio.insecure_channel(f"127.0.0.1:{servidor_grpc.port}") as canal:
        yield bff_runtime_grpc.RuntimeServiceStub(canal)


class TestTraducao:
    def test_turno_chega_ao_nucleo(self, cliente_rt, agente):
        r = cliente_rt.post(
            "/api/v1/demands/dem-1/threads/th-1/turns",
            headers={**CABECALHOS, "Idempotency-Key": "minha-chave"},
            json={"text": "por que está lento?", "task_kind": "investigation"},
        )
        assert r.status_code == 200
        assert r.json()["reply"].startswith("A lentidão")
        assert agente.RunTurn.pedidos[0].demand_id == "dem-1"

    def test_a_borda_nao_inventa_chave_de_idempotencia(self, cliente_rt, agente):
        """Turno gasta dinheiro.

        Em toda outra escrita a borda gera a chave, porque protege do retry do
        canal. Aqui NÃO: uma chave inventada transformaria retry de rede em
        consumo em dobro. É o cliente quem sabe se está retentando.
        """
        cliente_rt.post(
            "/api/v1/demands/dem-1/threads/th-1/turns",
            headers=CABECALHOS,
            json={"text": "oi"},
        )
        assert agente.RunTurn.pedidos[0].idempotency_key == ""

    def test_contexto_truncado_atravessa(self, cliente_rt):
        r = cliente_rt.post(
            "/api/v1/demands/dem-1/threads/th-1/turns",
            headers=CABECALHOS, json={"text": "oi"},
        )
        assert r.json()["context_truncated"] is True

    def test_cache_desconhecido_nao_vira_zero_afirmado(self, cliente_rt):
        """Zero com `cache_creation_known=false` é "não sei", não "não houve"."""
        u = cliente_rt.post(
            "/api/v1/demands/dem-1/threads/th-1/turns",
            headers=CABECALHOS, json={"text": "oi"},
        ).json()["usage"]
        assert u["cache_creation_tokens"] == 0
        assert u["cache_creation_known"] is False

    def test_justificativa_do_roteamento_vai_inteira(self, cliente_rt):
        """É a única parte auditável da decisão (ADR-0011 §3)."""
        r = cliente_rt.post(
            "/api/v1/demands/dem-1/threads/th-1/turns",
            headers=CABECALHOS, json={"text": "oi"},
        )
        assert "ADR-0011 §3" in r.json()["routing"]["reason"]

    def test_prazo_do_turno_e_maior_que_o_normal(self, cliente_rt, agente):
        """Turno leva minutos; o prazo normal do núcleo derrubaria todos."""
        cliente_rt.post(
            "/api/v1/demands/dem-1/threads/th-1/turns",
            headers=CABECALHOS, json={"text": "oi"},
        )
        assert agente.RunTurn.chamadas[0]["timeout"] > 60


class TestGRPC:
    async def test_paridade_com_o_rest(self, cliente_rt, stub_rt):
        rest = cliente_rt.post(
            "/api/v1/demands/dem-1/threads/th-1/turns",
            headers=CABECALHOS, json={"text": "oi", "task_kind": "investigation"},
        ).json()
        g = await stub_rt.RunTurn(
            bff.RunTurnRequest(
                demand_id="dem-1", thread_id="th-1", text="oi",
                task_kind="investigation", idempotency_key="k",
            ),
            metadata=CONTA,
        )
        assert g.reply == rest["reply"]
        assert g.routing.reason == rest["routing"]["reason"]
        assert g.context_truncated == rest["context_truncated"]

    async def test_sem_token(self, stub_rt):
        with pytest.raises(grpc.aio.AioRpcError) as e:
            await stub_rt.RunTurn(
                bff.RunTurnRequest(demand_id="d", thread_id="t", text="x"),
                metadata=metadados_de(None),
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
        achados = []
        for py in app.rglob("*.py"):
            # Os stubs gerados citam nomes de serviço do núcleo; o que importa
            # é o código escrito à mão.
            if "/gen/" in str(py):
                continue
            texto = py.read_text(encoding="utf-8")
            for termo in proibidos:
                if termo in texto:
                    achados.append(f"{py.relative_to(app)}: {termo}")
        assert not achados, (
            "o BFF não pode ter segredo nem falar com provedor de modelo "
            f"(ADR-0023): {achados}"
        )

    def test_o_runtime_nao_voltou_para_ca(self):
        app = pathlib.Path(__file__).resolve().parent.parent / "app"
        assert not (app / "runtime").exists(), (
            "app/runtime/ ressuscitou: o runtime vive no NÚCLEO (ADR-0023), "
            "onde a credencial não atravessa a rede"
        )
