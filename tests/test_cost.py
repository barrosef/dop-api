"""Cost on both transports, against a fake core.

Two tests carry this file, and both are written by searching the WHOLE
serialized body — `tests/test_resource.py`'s discipline, applied here to the two
facts that must not get lost on the way:

  - **money must not become a `float`.** The value in micros has to appear as an
    exact integer, and its decimal representation must NOT appear anywhere in
    the response: checking field by field would only catch the field somebody
    remembered to check, and one forgotten `/ 1_000_000` in a new field is
    enough for the cent to start disappearing;
  - **the routing's justification must not be truncated.** It arrives with the
    provenance up front and is the only auditable part of the decision.

The fakes and the fixtures live HERE, and not in `conftest.py`: there are other
agents writing in this repository right now.
"""

import grpc
import pytest
from fastapi.testclient import TestClient
from google.protobuf import timestamp_pb2

from app.coreclient import stubs
from app.coreclient.gen.dop.v1 import common_pb2, cost_pb2
from app.coreclient.resolver import CoreResolver
from app.grpcapi.cost import CostServicer
from app.grpcapi.gen.dop.bff.v1 import cost_pb2 as bff
from app.grpcapi.gen.dop.bff.v1 import cost_pb2_grpc as bff_grpc
from app.grpcapi.interceptors import (
    AuthInterceptor,
    ErrorInterceptor,
    LoggingInterceptor,
)
from app.main import create_app
from app.platform.security.firebase import FirebaseVerifier
from app.routers import cost as rotas
from tests.conftest import PROJECT, FakeCall, metadata_for, token_for

ACCOUNT = metadata_for(token_for())
REST_HEADERS = {"authorization": token_for(), "x-account-id": "acct-1"}

# 12.345678 USD. Chosen on purpose with decimal places a 64-bit float does not
# represent exactly and that a careless division would round.
COST_MICROS = 12_345_678
# This is the string that must NOT appear in the response: it is what is left
# when somebody "makes it easier for the screen" by dividing by a million.
COST_AS_DECIMAL = "12.345678"

# The justification as the core builds it: provenance + the row's why.
PROVENANCE = "ADR-0011 §3 (draft — calibrate with telemetry, P-7)"
REASON = (
    "you do not save on the critic — it is the brake (ADR-0007); saving on the "
    "brake returns the cost as a rejected PR, the most expensive rework in the flow"
)
JUSTIFICATIVA = f"{PROVENANCE}: {REASON}"


# ── núcleo fake ────────────────────────────────────────────────────────────


class OrcamentoPorEscopo(FakeCall):
    """A `GetBudget` that answers ACCORDING to the requested scope — as the core does.

    A double that always returned the same budget would let through an edge
    asking twice for the SAME scope: the attention box would show the
    teto da demand duas vezes e o da account nenhuma.
    """

    def __init__(self, by_scope: dict[str, cost_pb2.Budget]):
        super().__init__(None)
        self.by_scope = by_scope

    async def __call__(self, request, **kwargs):
        await super().__call__(request, **kwargs)
        return self.by_scope[request.scope]


class CustoFalso:
    """Núcleo fake de cost.

    The `Budget` it returns HAS a currency, just like the real core since
    `dop.v1.Budget.currency` came to exist (P-19). The case of a core that does
    not
    informa a moeda continua coberto — mas por um orçamento SEPARADO
    (`budget_without_currency`), and not by the default double: a mute double
    would make the "the edge does not invent USD" test pass by accident, and no
    test would prove it shows the currency when one arrives.
    """

    def __init__(self):
        self.budget = cost_pb2.Budget(
            scope="demand",
            scope_id="dem-1",
            limit_micros=50_000_000,
            spent_micros=COST_MICROS,
            currency="USD",
        )
        # With no currency: an older core, or a scope that does not have one yet.
        self.budget_without_currency = cost_pb2.Budget(
            scope="demand", scope_id="dem-1", limit_micros=50_000_000, spent_micros=COST_MICROS
        )
        self.uso = cost_pb2.UsageEvent(
            id="use-1",
            demand=common_pb2.DemandRef(id="dem-1"),
            thread_id="th-1",
            model="claude-opus",
            input_tokens=1200,
            output_tokens=340,
            cache_read_tokens=0,
            cache_creation_tokens=900,
            cost=common_pb2.Money(currency="USD", amount_micros=COST_MICROS),
            at=timestamp_pb2.Timestamp(seconds=1_770_000_000),
        )
        self.RouteModel = FakeCall(
            cost_pb2.RoutingDecision(
                task_kind="critic",
                model="claude-opus",
                effort="max",
                reason=JUSTIFICATIVA,
            )
        )
        self.GetBudget = FakeCall(self.budget)
        self.SetBudget = FakeCall(self.budget)
        self.RecordUsage = FakeCall(
            cost_pb2.RecordUsageResponse(recorded=True, budget_exceeded=False)
        )
        self.SummarizeCost = FakeCall(
            cost_pb2.SummarizeCostResponse(
                total=common_pb2.Money(currency="USD", amount_micros=COST_MICROS),
                cache_hit_ratio=0.42,
                recent=[self.uso],
            )
        )

    def estourou(self):
        """The core reporting that the ceiling was crossed."""
        self.RecordUsage.returns(
            cost_pb2.RecordUsageResponse(recorded=True, budget_exceeded=True)
        )
        self.GetBudget = OrcamentoPorEscopo(
            {
                "demand": cost_pb2.Budget(
                    scope="demand",
                    scope_id="dem-1",
                    limit_micros=10_000_000,
                    spent_micros=12_000_000,
                ),
                "account": cost_pb2.Budget(
                    scope="account",
                    scope_id="acct-1",
                    limit_micros=100_000_000,
                    spent_micros=12_000_000,
                ),
            }
        )

    def sem_teto(self):
        self.GetBudget.returns(
            cost_pb2.Budget(scope="account", scope_id="acct-1", limit_micros=0,
                            spent_micros=COST_MICROS)
        )


# ── fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def cost(core, monkeypatch):
    fake = CustoFalso()
    monkeypatch.setattr(stubs, "cost_stub", lambda: fake)
    return fake


def _app_com_rotas():
    """O app real do BFF, com as rotas deste domínio registradas.

    `app/main.py` belongs to the repository's owner and does not include this
    router yet (see the
    relatório). A checagem antes de incluir faz o teste continuar correto
    once the registration lands in `main`.
    """
    app = create_app()
    caminhos = {getattr(r, "path", "") for r in app.routes}
    if "/api/v1/cost/budget" not in caminhos:
        app.include_router(rotas.router)
    return app


@pytest.fixture
def client_cost(cost):
    with TestClient(_app_com_rotas()) as c:
        yield c


@pytest.fixture
async def stub_cost(cost):
    """Servidor gRPC real, em porta efêmera, com o servicer deste domínio.

    It does not reuse conftest's `grpc_server` because `GrpcServer` does not
    register this servicer yet (the registration is the repository owner's). The
    interceptor stack is the same — it is what makes the token, the context and
    the decorators
    valerem dentro do servicer.
    """
    server = grpc.aio.server(
        interceptors=(
            LoggingInterceptor(),
            ErrorInterceptor(),
            AuthInterceptor(FirebaseVerifier(PROJECT), CoreResolver()),
        )
    )
    bff_grpc.add_CostServiceServicer_to_server(CostServicer(), server)
    porta = server.add_insecure_port("127.0.0.1:0")
    await server.start()
    try:
        async with grpc.aio.insecure_channel(f"127.0.0.1:{porta}") as channel:
            yield bff_grpc.CostServiceStub(channel)
    finally:
        await server.stop(0)


# ── testes ──────────────────────────────────────────────────────────────────


class TestMoneyNeverBecomesAFloat:
    """Micros inteiros, com a moeda junto. Procurado no body inteiro."""

    def test_rest_returns_exact_micros_and_no_decimal(self, client_cost):
        r = client_cost.get("/api/v1/cost/summary", headers=REST_HEADERS)
        assert r.status_code == 200
        assert str(COST_MICROS) in r.text
        # If anybody divides by a million in ANY field, the decimal
        # representation shows up here — including in a field this test does not
        # even know about.
        assert COST_AS_DECIMAL not in r.text
        assert r.json()["total"] == {"currency": "USD", "amount_micros": COST_MICROS}

    def test_rest_carries_the_currency_alongside_the_value(self, client_cost):
        recente = client_cost.get(
            "/api/v1/cost/summary", headers=REST_HEADERS
        ).json()["recent"][0]
        assert recente["cost"]["currency"] == "USD"
        assert isinstance(recente["cost"]["amount_micros"], int)

    async def test_grpc_returns_an_int64_in_micros(self, stub_cost):
        resp = await stub_cost.SummarizeCost(
            bff.SummarizeCostRequest(), metadata=ACCOUNT
        )
        assert resp.total.amount_micros == COST_MICROS
        assert COST_AS_DECIMAL.encode() not in resp.SerializeToString()

    def test_the_budget_currency_comes_from_the_cores_field(self, client_cost):
        """`dop.v1.Budget.currency` existe (P-19) e a borda o LÊ.

        It used to read it with `getattr`, because the field was not in the
        contract. What this test guarantees is that the currency arrives
        alongside all three values — the limit, the spend and the remainder — and
        not only one of them: micros with no currency is a number with no unit in
        any of the three.
        """
        b = client_cost.get(
            "/api/v1/cost/budget?scope=demand&scope_id=dem-1", headers=REST_HEADERS
        ).json()
        assert b["limit"] == {"currency": "USD", "amount_micros": 50_000_000}
        assert b["spent"]["currency"] == "USD"
        assert b["remaining"]["currency"] == "USD"

    def test_the_edge_invents_no_currency_when_the_core_is_silent(
        self, client_cost, cost
    ):
        """An empty currency says "the core did not report" — and keeps saying it.

        The field coming to exist does not authorize filling the silence:
        guessing the core's default would be the edge asserting something it does
        not know, and the assertion would be right until the first account in
        another currency.
        """
        cost.GetBudget.returns(cost.budget_without_currency)
        b = client_cost.get(
            "/api/v1/cost/budget?scope=demand&scope_id=dem-1", headers=REST_HEADERS
        ).json()
        assert b["limit"]["currency"] == ""
        assert b["limit"]["amount_micros"] == 50_000_000


class TestTheRoutingJustification:
    """The decision comes WITH the why and the provenance, whole."""

    def test_rest_carries_the_whole_justification(self, client_cost):
        r = client_cost.get(
            "/api/v1/cost/routing?task_kind=critic", headers=REST_HEADERS
        )
        assert r.status_code == 200
        # No body inteiro: nem truncada, nem escapada, nem partida em campos.
        assert JUSTIFICATIVA in r.text
        assert r.json()["reason"] == JUSTIFICATIVA
        assert r.json()["model"] == "claude-opus"
        assert r.json()["effort"] == "max"

    def test_the_provenance_says_the_policy_is_a_draft(self, client_cost):
        """It is this sentence that stops anybody from treating the table as measurement."""
        reason = client_cost.get(
            "/api/v1/cost/routing?task_kind=critic", headers=REST_HEADERS
        ).json()["reason"]
        assert reason.startswith(PROVENANCE)
        assert "draft" in reason and "P-7" in reason

    async def test_grpc_carries_the_whole_justification(self, stub_cost):
        resp = await stub_cost.RouteModel(
            bff.RouteModelRequest(task_kind="critic"), metadata=ACCOUNT
        )
        assert resp.reason == JUSTIFICATIVA
        assert JUSTIFICATIVA.encode() in resp.SerializeToString()

    def test_the_demand_crosses_for_the_calibration(self, client_cost, cost):
        client_cost.get(
            "/api/v1/cost/routing?task_kind=critic&demand_id=dem-1",
            headers=REST_HEADERS,
        )
        assert cost.RouteModel.requests[0].demand_id == "dem-1"


class TestABlownBudget:
    """A SOFT cut (ADR-0011 §2): it pauses and asks, not a bare error."""

    def test_an_overrun_is_not_an_error_and_the_consumption_stays_recorded(self, client_cost, cost):
        cost.estourou()
        r = client_cost.post(
            "/api/v1/cost/usage",
            headers=REST_HEADERS,
            json={"model": "claude-opus", "demand_id": "dem-1", "cost_micros": 1},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["recorded"] is True
        assert body["budget_exceeded"] is True

    def test_the_notice_says_the_demand_pauses(self, client_cost, cost):
        """A bare "budget exceeded" would make every client reinvent the explanation."""
        cost.estourou()
        body = client_cost.post(
            "/api/v1/cost/usage",
            headers=REST_HEADERS,
            json={"model": "claude-opus", "demand_id": "dem-1", "cost_micros": 1},
        ).json()
        assert "PAUSES" in body["notice"]
        assert "ADR-0011" in body["notice"]
        # And the numbers the human decides with come along.
        assert [b["scope"] for b in body["budgets"]] == ["demand", "account"]
        assert body["budgets"][0]["spent"]["amount_micros"] == 12_000_000

    def test_with_no_overrun_it_spends_no_extra_round_trip_to_the_core(self, client_cost, cost):
        client_cost.post(
            "/api/v1/cost/usage",
            headers=REST_HEADERS,
            json={"model": "claude-opus", "demand_id": "dem-1", "cost_micros": 1},
        )
        assert cost.GetBudget.calls == []

    async def test_grpc_answers_ok_too(self, stub_cost, cost):
        cost.estourou()
        resp = await stub_cost.RecordUsage(
            bff.RecordUsageRequest(
                usage=bff.UsageEvent(model="claude-opus", demand_id="dem-1")
            ),
            metadata=ACCOUNT,
        )
        assert resp.recorded is True
        assert resp.budget_exceeded is True
        assert "PAUSES" in resp.notice

    def test_recording_carries_an_idempotency_key(self, client_cost, cost):
        """Obrigatória no núcleo: duplicata aqui viraria consumo legítimo."""
        client_cost.post(
            "/api/v1/cost/usage",
            headers=REST_HEADERS,
            json={"model": "claude-opus", "cost_micros": 1},
        )
        assert cost.RecordUsage.requests[0].idempotency_key != ""


class TestBudget:
    def test_the_remainder_is_integer_arithmetic(self, client_cost):
        b = client_cost.get(
            "/api/v1/cost/budget?scope=demand&scope_id=dem-1", headers=REST_HEADERS
        ).json()
        assert b["remaining"]["amount_micros"] == 50_000_000 - COST_MICROS

    def test_with_no_ceiling_the_remainder_is_null_not_zero(self, client_cost, cost):
        """Zero would mean "the money ran out", which is the opposite of "no ceiling"."""
        cost.sem_teto()
        b = client_cost.get("/api/v1/cost/budget", headers=REST_HEADERS).json()
        assert b["limit"]["amount_micros"] == 0
        assert b["remaining"] is None

    def test_setting_the_ceiling_requires_a_role(self, client_cost, core):
        """A budget is governance: the one who spends does not decide how much may be spent."""
        core.demote_to_developer()
        r = client_cost.put(
            "/api/v1/cost/budget",
            headers=REST_HEADERS,
            json={"scope": "demand", "scope_id": "dem-1", "limit_micros": 1_000_000},
        )
        assert r.status_code == 403

    def test_setting_the_ceiling_does_not_send_the_spend(self, client_cost, cost):
        """Aceitar `spent` do client permitiria zerar o gasto pedindo."""
        client_cost.put(
            "/api/v1/cost/budget",
            headers=REST_HEADERS,
            json={"scope": "demand", "scope_id": "dem-1", "limit_micros": 1_000_000},
        )
        assert cost.SetBudget.requests[0].budget.spent_micros == 0
        assert cost.SetBudget.requests[0].budget.limit_micros == 1_000_000

    def test_an_unknown_scope_is_a_422(self, client_cost):
        r = client_cost.put(
            "/api/v1/cost/budget",
            headers=REST_HEADERS,
            json={"scope": "galaxia", "limit_micros": 1},
        )
        assert r.status_code == 422

    def test_a_negative_limit_is_a_422(self, client_cost):
        r = client_cost.put(
            "/api/v1/cost/budget",
            headers=REST_HEADERS,
            json={"scope": "account", "limit_micros": -1},
        )
        assert r.status_code == 422

    async def test_setting_the_ceiling_requires_a_role_over_grpc(self, stub_cost, core):
        core.demote_to_developer()
        with pytest.raises(grpc.aio.AioRpcError) as e:
            await stub_cost.SetBudget(
                bff.SetBudgetRequest(scope="account", limit_micros=1), metadata=ACCOUNT
            )
        assert e.value.code() == grpc.StatusCode.PERMISSION_DENIED


class TestGRPC:
    async def test_with_no_token(self, stub_cost):
        with pytest.raises(grpc.aio.AioRpcError) as e:
            await stub_cost.GetBudget(
                bff.GetBudgetRequest(), metadata=metadata_for(None)
            )
        assert e.value.code() == grpc.StatusCode.UNAUTHENTICATED

    async def test_an_event_with_no_date_does_not_become_1970(self, stub_cost, cost):
        cost.SummarizeCost.returns(
            cost_pb2.SummarizeCostResponse(
                total=common_pb2.Money(currency="USD", amount_micros=1),
                recent=[cost_pb2.UsageEvent(id="use-2", model="claude-haiku")],
            )
        )
        resp = await stub_cost.SummarizeCost(
            bff.SummarizeCostRequest(), metadata=ACCOUNT
        )
        assert not resp.recent[0].HasField("at")

    def test_an_event_with_no_date_is_null_in_rest(self, client_cost, cost):
        cost.SummarizeCost.returns(
            cost_pb2.SummarizeCostResponse(
                total=common_pb2.Money(currency="USD", amount_micros=1),
                recent=[cost_pb2.UsageEvent(id="use-2", model="claude-haiku")],
            )
        )
        recente = client_cost.get(
            "/api/v1/cost/summary", headers=REST_HEADERS
        ).json()["recent"][0]
        assert recente["at"] is None


class TestParityBetweenTransports:
    """The alarm that fires if anybody reimplements the use case in an adapter."""

    async def test_the_budget_is_the_same_on_both_ports(self, client_cost, stub_cost):
        rest = client_cost.get(
            "/api/v1/cost/budget?scope=demand&scope_id=dem-1", headers=REST_HEADERS
        ).json()
        resp = await stub_cost.GetBudget(
            bff.GetBudgetRequest(scope="demand", scope_id="dem-1"), metadata=ACCOUNT
        )
        assert resp.scope == rest["scope"]
        assert resp.limit.amount_micros == rest["limit"]["amount_micros"]
        assert resp.spent.amount_micros == rest["spent"]["amount_micros"]
        assert resp.remaining.amount_micros == rest["remaining"]["amount_micros"]
        assert resp.HasField("remaining") is (rest["remaining"] is not None)

    async def test_the_routing_decision_is_the_same_on_both_ports(
        self, client_cost, stub_cost
    ):
        rest = client_cost.get(
            "/api/v1/cost/routing?task_kind=critic", headers=REST_HEADERS
        ).json()
        resp = await stub_cost.RouteModel(
            bff.RouteModelRequest(task_kind="critic"), metadata=ACCOUNT
        )
        assert resp.task_kind == rest["task_kind"]
        assert resp.model == rest["model"]
        assert resp.effort == rest["effort"]
        assert resp.reason == rest["reason"]
