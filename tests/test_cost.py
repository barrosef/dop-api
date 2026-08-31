"""Custo nos dois transportes, contra um núcleo falso.

Dois testes carregam o arquivo, e os dois são escritos procurando no corpo
serializado INTEIRO — a disciplina de `tests/test_resource.py`, aplicada aqui
aos dois fatos que não podem se perder no caminho:

  - **o dinheiro não pode virar `float`.** O valor em micros tem de aparecer
    como inteiro exato, e a representação decimal dele NÃO pode aparecer em
    lugar nenhum da resposta: procurar campo por campo só pegaria o campo que
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
from tests.conftest import PROJECT, ChamadaFalsa, metadados_de, token_de

CONTA = metadados_de(token_de())
CABECALHOS_REST = {"authorization": token_de(), "x-account-id": "acct-1"}

# 12,345678 USD. Escolhido de propósito com casas decimais que um float de 64
# bits não representa exatamente e que uma divisão descuidada arredondaria.
CUSTO_MICROS = 12_345_678
# É esta a string que NÃO pode aparecer na resposta: ela é o que sobra quando
# alguém "facilita para a tela" dividindo por um milhão.
CUSTO_EM_DECIMAL = "12.345678"

# A justificativa como o núcleo a monta: proveniência + o porquê da linha.
PROVENIENCIA = "ADR-0011 §3 (rascunho — calibrar com telemetria, P-7)"
MOTIVO = (
    "não se economiza no crítico — é o freio (ADR-0007); economizar no freio "
    "devolve o custo em PR reprovado, o retrabalho mais caro do fluxo"
)
JUSTIFICATIVA = f"{PROVENIENCIA}: {MOTIVO}"


# ── núcleo falso ────────────────────────────────────────────────────────────


class OrcamentoPorEscopo(ChamadaFalsa):
    """`GetBudget` que responde CONFORME o escopo pedido — como o núcleo faz.

    Um duplo que devolvesse sempre o mesmo orçamento deixaria passar a borda
    perguntando duas vezes pelo MESMO escopo: a caixa de atenção mostraria o
    teto da demanda duas vezes e o da conta nenhuma.
    """

    def __init__(self, por_escopo: dict[str, cost_pb2.Budget]):
        super().__init__(None)
        self.por_escopo = por_escopo

    async def __call__(self, request, **kwargs):
        await super().__call__(request, **kwargs)
        return self.por_escopo[request.scope]


class CustoFalso:
    """Núcleo falso de custo.

    O `Budget` que ele devolve NÃO tem moeda — igual ao núcleo de verdade, cujo
    `dop.v1.Budget` só carrega scope, scope_id e os dois inteiros. Um duplo com
    moeda faria o teste de "a borda não inventa USD" passar por acidente.
    """

    def __init__(self):
        self.orcamento = cost_pb2.Budget(
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
        self.RouteModel = ChamadaFalsa(
            cost_pb2.RoutingDecision(
                task_kind="critic",
                model="claude-opus",
                effort="max",
                reason=JUSTIFICATIVA,
            )
        )
        self.GetBudget = ChamadaFalsa(self.orcamento)
        self.SetBudget = ChamadaFalsa(self.orcamento)
        self.RecordUsage = ChamadaFalsa(
            cost_pb2.RecordUsageResponse(recorded=True, budget_exceeded=False)
        )
        self.SummarizeCost = ChamadaFalsa(
            cost_pb2.SummarizeCostResponse(
                total=common_pb2.Money(currency="USD", amount_micros=CUSTO_MICROS),
                cache_hit_ratio=0.42,
                recent=[self.uso],
            )
        )

    def estourou(self):
        """O núcleo avisando que o teto foi ultrapassado."""
        self.RecordUsage.devolve(
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
        self.GetBudget.devolve(
            cost_pb2.Budget(scope="account", scope_id="acct-1", limit_micros=0,
                            spent_micros=CUSTO_MICROS)
        )


# ── fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def custo(nucleo, monkeypatch):
    falso = CustoFalso()
    monkeypatch.setattr(stubs, "cost_stub", lambda: falso)
    return falso


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
def cliente_cost(custo):
    with TestClient(_app_com_rotas()) as c:
        yield c


@pytest.fixture
async def stub_cost(custo):
    """Servidor gRPC real, em porta efêmera, com o servicer deste domínio.

    Não reusa `servidor_grpc` do conftest porque `GrpcServer` ainda não
    registra este servicer (o registro é do dono do repositório). A pilha de
    interceptores é a mesma — é ela que faz token, contexto e decorators
    valerem dentro do servicer.
    """
    servidor = grpc.aio.server(
        interceptors=(
            LoggingInterceptor(),
            ErrorInterceptor(),
            AuthInterceptor(FirebaseVerifier(PROJECT), CoreResolver()),
        )
    )
    bff_grpc.add_CostServiceServicer_to_server(CostServicer(), servidor)
    porta = servidor.add_insecure_port("127.0.0.1:0")
    await servidor.start()
    try:
        async with grpc.aio.insecure_channel(f"127.0.0.1:{porta}") as canal:
            yield bff_grpc.CostServiceStub(canal)
    finally:
        await servidor.stop(0)


# ── testes ──────────────────────────────────────────────────────────────────


class TestDinheiroNaoViraFloat:
    """Micros inteiros, com a moeda junto. Procurado no corpo inteiro."""

    def test_rest_devolve_micros_exatos_e_nenhum_decimal(self, cliente_cost):
        r = cliente_cost.get("/api/v1/cost/summary", headers=CABECALHOS_REST)
        assert r.status_code == 200
        assert str(CUSTO_MICROS) in r.text
        # Se alguém dividir por um milhão em QUALQUER campo, a representação
        # decimal aparece aqui — inclusive num campo que este teste nem conhece.
        assert CUSTO_EM_DECIMAL not in r.text
        assert r.json()["total"] == {"currency": "USD", "amount_micros": CUSTO_MICROS}

    def test_rest_leva_a_moeda_junto_do_valor(self, cliente_cost):
        recente = cliente_cost.get(
            "/api/v1/cost/summary", headers=CABECALHOS_REST
        ).json()["recent"][0]
        assert recente["cost"]["currency"] == "USD"
        assert isinstance(recente["cost"]["amount_micros"], int)

    async def test_grpc_devolve_int64_em_micros(self, stub_cost):
        resp = await stub_cost.SummarizeCost(
            bff.SummarizeCostRequest(), metadata=CONTA
        )
        assert resp.total.amount_micros == CUSTO_MICROS
        assert CUSTO_EM_DECIMAL.encode() not in resp.SerializeToString()

    def test_a_borda_nao_inventa_moeda_no_orcamento(self, cliente_cost):
        """`dop.v1.Budget` ainda não carrega moeda — e "USD" não se chuta.

        Moeda vazia diz "o núcleo não informou". Preencher com o padrão do
        núcleo seria a borda afirmando algo que ela não sabe, e a afirmação
        ficaria certa até a primeira conta em BRL.
        """
        b = cliente_cost.get(
            "/api/v1/cost/budget?scope=demand&scope_id=dem-1", headers=CABECALHOS_REST
        ).json()
        assert b["limit"]["currency"] == ""
        assert b["limit"]["amount_micros"] == 50_000_000


class TestJustificativaDoRoteamento:
    """A decisão vem COM o porquê e a proveniência, inteiros."""

    def test_rest_carrega_a_justificativa_inteira(self, cliente_cost):
        r = cliente_cost.get(
            "/api/v1/cost/routing?task_kind=critic", headers=CABECALHOS_REST
        )
        assert r.status_code == 200
        # No corpo inteiro: nem truncada, nem escapada, nem partida em campos.
        assert JUSTIFICATIVA in r.text
        assert r.json()["reason"] == JUSTIFICATIVA
        assert r.json()["model"] == "claude-opus"
        assert r.json()["effort"] == "max"

    def test_a_proveniencia_diz_que_a_politica_e_rascunho(self, cliente_cost):
        """É esta frase que impede alguém de tratar a tabela como medida."""
        razao = cliente_cost.get(
            "/api/v1/cost/routing?task_kind=critic", headers=CABECALHOS_REST
        ).json()["reason"]
        assert razao.startswith(PROVENIENCIA)
        assert "rascunho" in razao and "P-7" in razao

    async def test_grpc_carrega_a_justificativa_inteira(self, stub_cost):
        resp = await stub_cost.RouteModel(
            bff.RouteModelRequest(task_kind="critic"), metadata=CONTA
        )
        assert resp.reason == JUSTIFICATIVA
        assert JUSTIFICATIVA.encode() in resp.SerializeToString()

    def test_demanda_atravessa_para_a_calibracao(self, cliente_cost, custo):
        cliente_cost.get(
            "/api/v1/cost/routing?task_kind=critic&demand_id=dem-1",
            headers=CABECALHOS_REST,
        )
        assert custo.RouteModel.pedidos[0].demand_id == "dem-1"


class TestOrcamentoEstourado:
    """Corte SUAVE (ADR-0011 §2): pausa e pergunta, não erro seco."""

    def test_estouro_nao_e_erro_e_o_consumo_fica_registrado(self, cliente_cost, custo):
        custo.estourou()
        r = cliente_cost.post(
            "/api/v1/cost/usage",
            headers=CABECALHOS_REST,
            json={"model": "claude-opus", "demand_id": "dem-1", "cost_micros": 1},
        )
        assert r.status_code == 200
        corpo = r.json()
        assert corpo["recorded"] is True
        assert corpo["budget_exceeded"] is True

    def test_o_aviso_diz_que_a_demanda_pausa(self, cliente_cost, custo):
        """Um "budget exceeded" seco faria cada cliente reinventar a explicação."""
        custo.estourou()
        corpo = cliente_cost.post(
            "/api/v1/cost/usage",
            headers=CABECALHOS_REST,
            json={"model": "claude-opus", "demand_id": "dem-1", "cost_micros": 1},
        ).json()
        assert "PAUSA" in corpo["notice"]
        assert "ADR-0011" in corpo["notice"]
        # E os números com que o humano decide vêm junto.
        assert [b["scope"] for b in corpo["budgets"]] == ["demand", "account"]
        assert corpo["budgets"][0]["spent"]["amount_micros"] == 12_000_000

    def test_sem_estouro_nao_gasta_ida_extra_ao_nucleo(self, cliente_cost, custo):
        cliente_cost.post(
            "/api/v1/cost/usage",
            headers=CABECALHOS_REST,
            json={"model": "claude-opus", "demand_id": "dem-1", "cost_micros": 1},
        )
        assert custo.GetBudget.chamadas == []

    async def test_grpc_tambem_responde_ok(self, stub_cost, custo):
        custo.estourou()
        resp = await stub_cost.RecordUsage(
            bff.RecordUsageRequest(
                usage=bff.UsageEvent(model="claude-opus", demand_id="dem-1")
            ),
            metadata=CONTA,
        )
        assert resp.recorded is True
        assert resp.budget_exceeded is True
        assert "PAUSA" in resp.notice

    def test_registro_carrega_idempotencia(self, cliente_cost, custo):
        """Obrigatória no núcleo: duplicata aqui viraria consumo legítimo."""
        cliente_cost.post(
            "/api/v1/cost/usage",
            headers=CABECALHOS_REST,
            json={"model": "claude-opus", "cost_micros": 1},
        )
        assert custo.RecordUsage.pedidos[0].idempotency_key != ""


class TestOrcamento:
    def test_sobra_e_aritmetica_de_inteiro(self, cliente_cost):
        b = cliente_cost.get(
            "/api/v1/cost/budget?scope=demand&scope_id=dem-1", headers=CABECALHOS_REST
        ).json()
        assert b["remaining"]["amount_micros"] == 50_000_000 - CUSTO_MICROS

    def test_sem_teto_a_sobra_e_nula_nao_zero(self, cliente_cost, custo):
        """Zero significaria "acabou o dinheiro", que é o oposto de "sem teto"."""
        custo.sem_teto()
        b = cliente_cost.get("/api/v1/cost/budget", headers=CABECALHOS_REST).json()
        assert b["limit"]["amount_micros"] == 0
        assert b["remaining"] is None

    def test_definir_teto_exige_papel(self, cliente_cost, nucleo):
        """Orçamento é governança: quem gasta não decide quanto pode gastar."""
        nucleo.papel_developer()
        r = cliente_cost.put(
            "/api/v1/cost/budget",
            headers=CABECALHOS_REST,
            json={"scope": "demand", "scope_id": "dem-1", "limit_micros": 1_000_000},
        )
        assert r.status_code == 403

    def test_definir_teto_nao_manda_o_gasto(self, cliente_cost, custo):
        """Aceitar `spent` do cliente permitiria zerar o gasto pedindo."""
        cliente_cost.put(
            "/api/v1/cost/budget",
            headers=CABECALHOS_REST,
            json={"scope": "demand", "scope_id": "dem-1", "limit_micros": 1_000_000},
        )
        assert custo.SetBudget.pedidos[0].budget.spent_micros == 0
        assert custo.SetBudget.pedidos[0].budget.limit_micros == 1_000_000

    def test_escopo_desconhecido_e_422(self, cliente_cost):
        r = cliente_cost.put(
            "/api/v1/cost/budget",
            headers=CABECALHOS_REST,
            json={"scope": "galaxia", "limit_micros": 1},
        )
        assert r.status_code == 422

    def test_limite_negativo_e_422(self, cliente_cost):
        r = cliente_cost.put(
            "/api/v1/cost/budget",
            headers=CABECALHOS_REST,
            json={"scope": "account", "limit_micros": -1},
        )
        assert r.status_code == 422

    async def test_definir_teto_exige_papel_no_grpc(self, stub_cost, nucleo):
        nucleo.papel_developer()
        with pytest.raises(grpc.aio.AioRpcError) as e:
            await stub_cost.SetBudget(
                bff.SetBudgetRequest(scope="account", limit_micros=1), metadata=CONTA
            )
        assert e.value.code() == grpc.StatusCode.PERMISSION_DENIED


class TestGRPC:
    async def test_sem_token(self, stub_cost):
        with pytest.raises(grpc.aio.AioRpcError) as e:
            await stub_cost.GetBudget(
                bff.GetBudgetRequest(), metadata=metadados_de(None)
            )
        assert e.value.code() == grpc.StatusCode.UNAUTHENTICATED

    async def test_evento_sem_data_nao_vira_1970(self, stub_cost, custo):
        custo.SummarizeCost.devolve(
            cost_pb2.SummarizeCostResponse(
                total=common_pb2.Money(currency="USD", amount_micros=1),
                recent=[cost_pb2.UsageEvent(id="use-2", model="claude-haiku")],
            )
        )
        resp = await stub_cost.SummarizeCost(
            bff.SummarizeCostRequest(), metadata=CONTA
        )
        assert not resp.recent[0].HasField("at")

    def test_evento_sem_data_e_nulo_no_rest(self, cliente_cost, custo):
        custo.SummarizeCost.devolve(
            cost_pb2.SummarizeCostResponse(
                total=common_pb2.Money(currency="USD", amount_micros=1),
                recent=[cost_pb2.UsageEvent(id="use-2", model="claude-haiku")],
            )
        )
        recente = cliente_cost.get(
            "/api/v1/cost/summary", headers=CABECALHOS_REST
        ).json()["recent"][0]
        assert recente["at"] is None


class TestParidadeEntreTransportes:
    """O alarme que dispara se alguém reimplementar o caso de uso num adaptador."""

    async def test_orcamento_igual_nas_duas_portas(self, cliente_cost, stub_cost):
        rest = cliente_cost.get(
            "/api/v1/cost/budget?scope=demand&scope_id=dem-1", headers=CABECALHOS_REST
        ).json()
        resp = await stub_cost.GetBudget(
            bff.GetBudgetRequest(scope="demand", scope_id="dem-1"), metadata=CONTA
        )
        assert resp.scope == rest["scope"]
        assert resp.limit.amount_micros == rest["limit"]["amount_micros"]
        assert resp.spent.amount_micros == rest["spent"]["amount_micros"]
        assert resp.remaining.amount_micros == rest["remaining"]["amount_micros"]
        assert resp.HasField("remaining") is (rest["remaining"] is not None)

    async def test_decisao_de_roteamento_igual_nas_duas_portas(
        self, cliente_cost, stub_cost
    ):
        rest = cliente_cost.get(
            "/api/v1/cost/routing?task_kind=critic", headers=CABECALHOS_REST
        ).json()
        resp = await stub_cost.RouteModel(
            bff.RouteModelRequest(task_kind="critic"), metadata=CONTA
        )
        assert resp.task_kind == rest["task_kind"]
        assert resp.model == rest["model"]
        assert resp.effort == rest["effort"]
        assert resp.reason == rest["reason"]
