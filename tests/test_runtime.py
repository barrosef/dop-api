"""The turn-running edge — and the invariant that it holds no secret.

The runtime was moved to the core (ADR-0023). What is left here is translation,
and the tests reflect that: the providers' contract suite lives in dop-core, in
`test/contract/agentprovider.go`, next to the code it exercises.

This file's last test is the most important one and has nothing to do with
translation: it guards the invariant that motivated the change.
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
    """A fake core of the AgentService — the turn happens THERE, not here."""

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
                reply="The slowness comes from the missing index.",
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


class TestTranslation:
    def test_the_turn_reaches_the_core(self, client_rt, agente):
        r = client_rt.post(
            "/api/v1/demands/dem-1/threads/th-1/turns",
            headers={**HEADERS, "Idempotency-Key": "minha-key"},
            json={"text": "why is it slow?", "task_kind": "investigation"},
        )
        assert r.status_code == 200
        assert r.json()["reply"].startswith("The slowness")
        assert agente.RunTurn.requests[0].demand_id == "dem-1"

    def test_the_edge_does_not_invent_an_idempotency_key(self, client_rt, agente):
        """Turno gasta dinheiro.

        On every other write the edge generates the key, because it protects
        against the channel's retry. Here it does NOT: an invented key would turn
        a network retry into double consumption. It is the client that knows
        whether it is retrying.
        """
        client_rt.post(
            "/api/v1/demands/dem-1/threads/th-1/turns",
            headers=HEADERS,
            json={"text": "oi"},
        )
        assert agente.RunTurn.requests[0].idempotency_key == ""

    def test_a_truncated_context_crosses(self, client_rt):
        r = client_rt.post(
            "/api/v1/demands/dem-1/threads/th-1/turns",
            headers=HEADERS, json={"text": "oi"},
        )
        assert r.json()["context_truncated"] is True

    def test_an_unknown_cache_does_not_become_an_asserted_zero(self, client_rt):
        """A zero with `cache_creation_known=false` is "I do not know", not "there was none"."""
        u = client_rt.post(
            "/api/v1/demands/dem-1/threads/th-1/turns",
            headers=HEADERS, json={"text": "oi"},
        ).json()["usage"]
        assert u["cache_creation_tokens"] == 0
        assert u["cache_creation_known"] is False

    def test_the_routing_justification_goes_whole(self, client_rt):
        """It is the only auditable part of the decision (ADR-0011 §3)."""
        r = client_rt.post(
            "/api/v1/demands/dem-1/threads/th-1/turns",
            headers=HEADERS, json={"text": "oi"},
        )
        assert "ADR-0011 §3" in r.json()["routing"]["reason"]

    def test_the_turns_deadline_is_longer_than_the_normal_one(self, client_rt, agente):
        """Turno leva minutos; o prazo normal do núcleo derrubaria todos."""
        client_rt.post(
            "/api/v1/demands/dem-1/threads/th-1/turns",
            headers=HEADERS, json={"text": "oi"},
        )
        assert agente.RunTurn.calls[0]["timeout"] > 60


class TestGRPC:
    async def test_parity_with_rest(self, client_rt, stub_rt):
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

    async def test_with_no_token(self, stub_rt):
        with pytest.raises(grpc.aio.AioRpcError) as e:
            await stub_rt.RunTurn(
                bff.RunTurnRequest(demand_id="d", thread_id="t", text="x"),
                metadata=metadata_for(None),
            )
        assert e.value.code() == grpc.StatusCode.UNAUTHENTICATED


class TestTheBffHoldsNoSecret:
    """The invariant that motivated ADR-0023, turned into a test (P-22).

    A sibling of "the BFF has no database": this layer is the one exposed to the
    internet, and compromising it must not hand over anybody's credential.
    Discipline is not enough — somebody adds an `import` in good faith and nobody
    notices in review.
    """

    def test_no_module_touches_the_vault_or_a_provider_key(self):
        forbidden = [
            "secretstore",
            "SecretStore",
            "ANTHROPIC_API_KEY",
            "OPENAI_API_KEY",
            "anthropic",
        ]
        app = pathlib.Path(__file__).resolve().parent.parent / "app"
        findings = []
        for py in app.rglob("*.py"):
            # The generated stubs mention the core's service names; what matters
            # is the hand-written code.
            if "/gen/" in str(py):
                continue
            text = py.read_text(encoding="utf-8")
            for term in forbidden:
                if term in text:
                    findings.append(f"{py.relative_to(app)}: {term}")
        assert not findings, (
            "the BFF must hold no secret and must not talk to a model provider "
            f"(ADR-0023): {findings}"
        )

    def test_the_runtime_has_not_come_back_here(self):
        app = pathlib.Path(__file__).resolve().parent.parent / "app"
        assert not (app / "runtime").exists(), (
            "app/runtime/ came back: the runtime lives in the CORE (ADR-0023), "
            "where the credential crosses no network"
        )
