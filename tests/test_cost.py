"""Custo nos dois transportes, contra um núcleo fake.

Dois testes carregam o arquivo, e os dois são escritos procurando no body
serializado INTEIRO — a disciplina de `tests/test_resource.py`, aplicada aqui
aos dois fatos que não podem se perder no caminho:

  - **o dinheiro não pode virar `float`.** O value em micros tem de aparecer
    como inteiro exato, e a representação decimal dele NÃO pode aparecer em
    lugar nenhum da response: procurar campo por campo só pegaria o campo que
    alguém lembrou de conferir, e basta um `/ 1_000_000` esquecido num campo
    novo para o centavo começar a sumir;
  - **a justificativa do roteamento não pode ser truncada.** Ela chega com a
    proveniência na frente e é a única parte auditável da decisão.

Fakes e fixtures ficam AQUI, e não no `conftest.py`: há outros agentes
escrevendo neste repositório agora.
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

# 12,345678 USD. Escolhido de propósito com casas decimais que um float de 64
# bits não representa exatamente e que uma divisão descuidada arredondaria.
CUSTO_MICROS = 12_345_678
# É esta a string que NÃO pode aparecer na response: ela é o que sobra quando
# alguém "facilita para a screen" dividindo por um milhão.
CUSTO_EM_DECIMAL = "12.345678"

# A justificativa como o núcleo a monta: proveniência + o porquê da linha.
PROVENIENCIA = "ADR-0011 §3 (rascunho — calibrar com telemetria, P-7)"
MOTIVO = (
    "não se economiza no crítico — é o freio (ADR-0007); economizar no freio "
    "returns o custo em PR reprovado, o retrabalho mais caro do flow"
)
JUSTIFICATIVA = f"{PROVENIENCIA}: {MOTIVO}"


# ── núcleo fake ────────────────────────────────────────────────────────────


class OrcamentoPorEscopo(FakeCall):
    """`GetBudget` que responde CONFORME o scope request — como o núcleo faz.

    Um double que devolvesse sempre o mesmo orçamento deixaria passar a borda
    perguntando duas vezes pelo MESMO scope: a box de atenção mostraria o
    teto da demand duas vezes e o da account nenhuma.
    """

    def __init__(self, by_scope: dict[str, cost_pb2.Budget]):
        super().__init__(None)
        self.by_scope = by_scope

    async def __call__(self, request, **kwargs):
        await super().__call__(request, **kwargs)
        return self.by_scope[request.scope]


class CustoFalso:
    """Núcleo fake de custo.

    O `Budget` que ele returns TEM moeda, igual ao núcleo de verdade desde que
    `dop.v1.Budget.currency` passou a existir (P-19). O caso do núcleo que não
    informa a moeda continua coberto — mas por um orçamento SEPARADO
    (`orcamento_sem_moeda`), e não pelo double padrão: um double mudo faria o
    teste de "a borda não inventa USD" passar por acidente, e nenhum teste
    provaria que ela mostra a moeda quando ela vem.
    """

    def __init__(self):
        self.orcamento = cost_pb2.Budget(
            scope="demand",
            scope_id="dem-1",
            limit_micros=50_000_000,
            spent_micros=CUSTO_MICROS,
            currency="USD",
        )
        # Sem moeda: o núcleo antigo, ou o scope que ainda não a tem.
        self.orcamento_sem_moeda = cost_pb2.Budget(
            scope="demand", scope_id="dem-1", limit_micros=50_000_000, spent_micros=CUSTO_MICROS
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
            cost=common_pb2.Money(currency="USD", amount_micros=CUSTO_MICROS),
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
        self.GetBudget = FakeCall(self.orcamento)
        self.SetBudget = FakeCall(self.orcamento)
        self.RecordUsage = FakeCall(
            cost_pb2.RecordUsageResponse(recorded=True, budget_exceeded=False)
        )
        self.SummarizeCost = FakeCall(
            cost_pb2.SummarizeCostResponse(
                total=common_pb2.Money(currency="USD", amount_micros=CUSTO_MICROS),
                cache_hit_ratio=0.42,
                recent=[self.uso],
            )
        )

    def estourou(self):
        """O núcleo avisando que o teto foi ultrapassado."""
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
                            spent_micros=CUSTO_MICROS)
        )


# ── fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def custo(core, monkeypatch):
    fake = CustoFalso()
    monkeypatch.setattr(stubs, "cost_stub", lambda: fake)
    return fake


def _app_com_rotas():
    """O app real do BFF, com as rotas deste domínio registradas.

    `app/main.py` é do dono do repositório e ainda não inclui este router (ver o
    relatório). A checagem antes de incluir faz o teste continuar correto
    depois que o registro entrar no `main`.
    """
    app = create_app()
    caminhos = {getattr(r, "path", "") for r in app.routes}
    if "/api/v1/cost/budget" not in caminhos:
        app.include_router(rotas.router)
    return app


@pytest.fixture
def client_cost(custo):
    with TestClient(_app_com_rotas()) as c:
        yield c


@pytest.fixture
async def stub_cost(custo):
    """Servidor gRPC real, em porta efêmera, com o servicer deste domínio.

    Não reusa `grpc_server` do conftest porque `GrpcServer` ainda não
    registra este servicer (o registro é do dono do repositório). A pilha de
    interceptores é a mesma — é ela que faz token, context e decorators
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
        assert str(CUSTO_MICROS) in r.text
        # Se alguém dividir por um milhão em QUALQUER campo, a representação
        # decimal aparece aqui — inclusive num campo que este teste nem conhece.
        assert CUSTO_EM_DECIMAL not in r.text
        assert r.json()["total"] == {"currency": "USD", "amount_micros": CUSTO_MICROS}

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
        assert resp.total.amount_micros == CUSTO_MICROS
        assert CUSTO_EM_DECIMAL.encode() not in resp.SerializeToString()

    def test_the_budget_currency_comes_from_the_cores_field(self, client_cost):
        """`dop.v1.Budget.currency` existe (P-19) e a borda o LÊ.

        Antes ela lia com `getattr`, porque o campo não estava no contrato. O
        que este teste garante é que a moeda chega junto dos três valores —
        limite, gasto e sobra —, e não só de um deles: micros sem moeda é
        número sem unidade em qualquer um dos três.
        """
        b = client_cost.get(
            "/api/v1/cost/budget?scope=demand&scope_id=dem-1", headers=REST_HEADERS
        ).json()
        assert b["limit"] == {"currency": "USD", "amount_micros": 50_000_000}
        assert b["spent"]["currency"] == "USD"
        assert b["remaining"]["currency"] == "USD"

    def test_the_edge_invents_no_currency_when_the_core_is_silent(
        self, client_cost, custo
    ):
        """Moeda vazia diz "o núcleo não informou" — e continua dizendo isso.

        O campo passar a existir não autoriza preencher o silêncio: chutar o
        padrão do núcleo seria a borda afirmando algo que ela não sabe, e a
        afirmação ficaria certa até a primeira account em BRL.
        """
        custo.GetBudget.returns(custo.orcamento_sem_moeda)
        b = client_cost.get(
            "/api/v1/cost/budget?scope=demand&scope_id=dem-1", headers=REST_HEADERS
        ).json()
        assert b["limit"]["currency"] == ""
        assert b["limit"]["amount_micros"] == 50_000_000


class TestTheRoutingJustification:
    """A decisão vem COM o porquê e a proveniência, inteiros."""

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
        """É esta frase que impede alguém de tratar a tabela como medida."""
        razao = client_cost.get(
            "/api/v1/cost/routing?task_kind=critic", headers=REST_HEADERS
        ).json()["reason"]
        assert razao.startswith(PROVENIENCIA)
        assert "rascunho" in razao and "P-7" in razao

    async def test_grpc_carries_the_whole_justification(self, stub_cost):
        resp = await stub_cost.RouteModel(
            bff.RouteModelRequest(task_kind="critic"), metadata=ACCOUNT
        )
        assert resp.reason == JUSTIFICATIVA
        assert JUSTIFICATIVA.encode() in resp.SerializeToString()

    def test_the_demand_crosses_for_the_calibration(self, client_cost, custo):
        client_cost.get(
            "/api/v1/cost/routing?task_kind=critic&demand_id=dem-1",
            headers=REST_HEADERS,
        )
        assert custo.RouteModel.requests[0].demand_id == "dem-1"


class TestABlownBudget:
    """Corte SUAVE (ADR-0011 §2): pausa e pergunta, não err seco."""

    def test_an_overrun_is_not_an_error_and_the_consumption_stays_recorded(self, client_cost, custo):
        custo.estourou()
        r = client_cost.post(
            "/api/v1/cost/usage",
            headers=REST_HEADERS,
            json={"model": "claude-opus", "demand_id": "dem-1", "cost_micros": 1},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["recorded"] is True
        assert body["budget_exceeded"] is True

    def test_the_notice_says_the_demand_pauses(self, client_cost, custo):
        """Um "budget exceeded" seco faria cada client reinventar a explicação."""
        custo.estourou()
        body = client_cost.post(
            "/api/v1/cost/usage",
            headers=REST_HEADERS,
            json={"model": "claude-opus", "demand_id": "dem-1", "cost_micros": 1},
        ).json()
        assert "PAUSES" in body["notice"]
        assert "ADR-0011" in body["notice"]
        # E os números com que o humano decide vêm junto.
        assert [b["scope"] for b in body["budgets"]] == ["demand", "account"]
        assert body["budgets"][0]["spent"]["amount_micros"] == 12_000_000

    def test_with_no_overrun_it_spends_no_extra_round_trip_to_the_core(self, client_cost, custo):
        client_cost.post(
            "/api/v1/cost/usage",
            headers=REST_HEADERS,
            json={"model": "claude-opus", "demand_id": "dem-1", "cost_micros": 1},
        )
        assert custo.GetBudget.calls == []

    async def test_grpc_answers_ok_too(self, stub_cost, custo):
        custo.estourou()
        resp = await stub_cost.RecordUsage(
            bff.RecordUsageRequest(
                usage=bff.UsageEvent(model="claude-opus", demand_id="dem-1")
            ),
            metadata=ACCOUNT,
        )
        assert resp.recorded is True
        assert resp.budget_exceeded is True
        assert "PAUSES" in resp.notice

    def test_recording_carries_an_idempotency_key(self, client_cost, custo):
        """Obrigatória no núcleo: duplicata aqui viraria consumo legítimo."""
        client_cost.post(
            "/api/v1/cost/usage",
            headers=REST_HEADERS,
            json={"model": "claude-opus", "cost_micros": 1},
        )
        assert custo.RecordUsage.requests[0].idempotency_key != ""


class TestBudget:
    def test_the_remainder_is_integer_arithmetic(self, client_cost):
        b = client_cost.get(
            "/api/v1/cost/budget?scope=demand&scope_id=dem-1", headers=REST_HEADERS
        ).json()
        assert b["remaining"]["amount_micros"] == 50_000_000 - CUSTO_MICROS

    def test_with_no_ceiling_the_remainder_is_null_not_zero(self, client_cost, custo):
        """Zero significaria "acabou o dinheiro", que é o oposto de "sem teto"."""
        custo.sem_teto()
        b = client_cost.get("/api/v1/cost/budget", headers=REST_HEADERS).json()
        assert b["limit"]["amount_micros"] == 0
        assert b["remaining"] is None

    def test_setting_the_ceiling_requires_a_role(self, client_cost, core):
        """Orçamento é governança: quem gasta não decide quanto pode gastar."""
        core.demote_to_developer()
        r = client_cost.put(
            "/api/v1/cost/budget",
            headers=REST_HEADERS,
            json={"scope": "demand", "scope_id": "dem-1", "limit_micros": 1_000_000},
        )
        assert r.status_code == 403

    def test_setting_the_ceiling_does_not_send_the_spend(self, client_cost, custo):
        """Aceitar `spent` do client permitiria zerar o gasto pedindo."""
        client_cost.put(
            "/api/v1/cost/budget",
            headers=REST_HEADERS,
            json={"scope": "demand", "scope_id": "dem-1", "limit_micros": 1_000_000},
        )
        assert custo.SetBudget.requests[0].budget.spent_micros == 0
        assert custo.SetBudget.requests[0].budget.limit_micros == 1_000_000

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

    async def test_an_event_with_no_date_does_not_become_1970(self, stub_cost, custo):
        custo.SummarizeCost.returns(
            cost_pb2.SummarizeCostResponse(
                total=common_pb2.Money(currency="USD", amount_micros=1),
                recent=[cost_pb2.UsageEvent(id="use-2", model="claude-haiku")],
            )
        )
        resp = await stub_cost.SummarizeCost(
            bff.SummarizeCostRequest(), metadata=ACCOUNT
        )
        assert not resp.recent[0].HasField("at")

    def test_an_event_with_no_date_is_null_in_rest(self, client_cost, custo):
        custo.SummarizeCost.returns(
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
    """O alarme que dispara se alguém reimplementar o caso de uso num adaptador."""

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
