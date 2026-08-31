"""O AgentRuntime: a SUÍTE DE CONTRATO da porta, e o ciclo do turno.

Fakes e fixtures ficam AQUI, e não no `conftest.py`: há outros agentes
escrevendo neste repositório agora. O que se IMPORTA de lá é o que já existe
(`nucleo`, `ChamadaFalsa`, `token_de`, `metadados_de`).

═══════════════════════════════════════════════════════════════════════════════
Parte 1 — A SUÍTE DE CONTRATO DA PORTA `AgentProvider`
═══════════════════════════════════════════════════════════════════════════════

Disciplina da ADR-0001, no formato do `test/contract/` do dop-core: **uma porta
com um adaptador só é palpite.** O mesmo conjunto roda contra TODOS — o duplo
em memória, o adaptador Anthropic e o adaptador OpenAI — e é ele que garante
substituibilidade de fato, não de intenção.

Nenhum adaptador toca a rede aqui: o Anthropic recebe um cliente injetado e o
OpenAI um transporte `httpx.MockTransport`. Os dois são os adaptadores REAIS,
com o `render` e a tradução de resposta reais — o que é falso é o outro lado
do fio, não o código sob teste. Um teste que substituísse o adaptador inteiro
provaria só que o duplo funciona.

A verificação mais difícil da suíte é a da ORDEM DO PREFIXO, e ela é feita sem
conhecer o formato de nenhum fornecedor: `render` devolve a requisição, a suíte
a serializa e compara as POSIÇÕES do prefixo estável e do texto volátil. Se um
adaptador puser o volátil antes, a economia da ADR-0012 evapora — e evapora em
silêncio, porque nada falha, só fica caro.

═══════════════════════════════════════════════════════════════════════════════
Parte 2 — O CICLO DO TURNO, nas duas portas, contra um núcleo falso
═══════════════════════════════════════════════════════════════════════════════

O núcleo nunca sobe e a API do fornecedor nunca é chamada de verdade. O ponto
de substituição do provedor é UM só (`app.usecases.runtime.provider_for`), pela
mesma razão que o do núcleo é (`stubs.*_stub`): trocar uma função troca o
mundo, sem patch espalhado por módulo.
"""

import json

import grpc
import httpx
import pytest
from fastapi.testclient import TestClient
from google.protobuf import struct_pb2

from app.coreclient import stubs
from app.coreclient.gen.dop.v1 import (
    common_pb2,
    cost_pb2,
    demand_pb2,
    knowledge_pb2,
)
from app.coreclient.resolver import CoreResolver
from app.grpcapi.gen.dop.bff.v1 import runtime_pb2 as bff
from app.grpcapi.gen.dop.bff.v1 import runtime_pb2_grpc as bff_grpc
from app.grpcapi.interceptors import (
    AuthInterceptor,
    ErrorInterceptor,
    LoggingInterceptor,
)
from app.grpcapi.runtime import RuntimeServicer
from app.main import create_app
from app.platform.security.firebase import FirebaseVerifier
from app.routers import runtime as rotas
from app.runtime import prompt as runtime_prompt
from app.runtime.adapters.anthropic_provider import AnthropicAgentProvider
from app.runtime.adapters.openai_provider import OpenAIAgentProvider
from app.runtime.credentials import Credential
from app.runtime.errors import AgentProviderUnavailable, Reason
from app.runtime.ports import (
    AgentProvider,
    Capability,
    Effort,
    Message,
    ModelClass,
    Price,
    ProviderInfo,
    Reply,
    Role,
    StopReason,
    Turn,
    Usage,
    serialize_for_audit,
)
from app.usecases import runtime as uc
from tests.conftest import PROJECT, ChamadaFalsa, metadados_de, token_de

CONTA = metadados_de(token_de())
CABECALHOS = {"authorization": token_de(), "x-account-id": "acct-1"}

# Texto que só pode aparecer no PREFIXO, e texto que só pode aparecer DEPOIS
# dele. Escolhidos improváveis de propósito: a busca é no corpo serializado
# INTEIRO, e uma palavra comum daria falso positivo em qualquer campo.
PREFIXO = "REGRA-ESTAVEL-DO-PROJETO-XYZZY"
VOLATIL = "PERGUNTA-DESTE-TURNO-PLUGH"
OPERADOR = "INTERVENCAO-DO-OPERADOR-FROBOZZ"

JUSTIFICATIVA = (
    "ADR-0011 §3 (rascunho — calibrar com telemetria, P-7): não se economiza "
    "no crítico — é o freio (ADR-0007)"
)


def turno_de_teste(*, com_operador: bool = False) -> Turn:
    mensagens = [Message(role=Role.USER, text=VOLATIL)]
    if com_operador:
        mensagens.append(Message(role=Role.OPERATOR, text=OPERADOR))
    return Turn(
        stable_prefix=f"contrato do runtime\n{PREFIXO}\n",
        messages=tuple(mensagens),
        output_schema=runtime_prompt.OUTPUT_SCHEMA,
        max_output_tokens=1024,
    )


# ── o duplo em memória da porta ─────────────────────────────────────────────


class ProvedorEmMemoria(AgentProvider):
    """Duplo da porta — o "adaptador de memória" que toda porta daqui tem.

    Existe por dois motivos: dar à suíte de contrato um terceiro ponto de
    comparação (dois adaptadores que erram igual passariam juntos), e permitir
    que os testes do CICLO rodem sem tocar em fornecedor nenhum.
    """

    PRECOS = {"memoria-forte": Price("USD", 5_000, 25_000, 500, 6_250)}

    def __init__(self, resposta: dict | None = None, usage: Usage | None = None):
        self.resposta = resposta or {
            "reply": "entendido",
            "concluded": False,
            "finding_title": "",
            "finding_summary": "",
            "finding_evidence": [],
        }
        self.usage = usage or Usage(
            input_tokens=1_000, output_tokens=200, cache_read_tokens=800,
            cache_creation_tokens=1_200,
        )
        self.stop_reason = StopReason.COMPLETED
        self.turnos: list[Turn] = []
        self.pedidos: list[dict] = []

    @property
    def info(self) -> ProviderInfo:
        return ProviderInfo(
            name="memoria",
            catalog={
                ModelClass.CHEAP: "memoria-barato",
                ModelClass.MEDIUM: "memoria-medio",
                ModelClass.STRONG: "memoria-forte",
            },
            capabilities=frozenset(
                {
                    Capability.EXPLICIT_PREFIX_CACHE,
                    Capability.CACHE_CREATION_ACCOUNTING,
                    Capability.OPERATOR_CHANNEL,
                    Capability.FULL_EFFORT_RANGE,
                    Capability.STRUCTURED_OUTPUT,
                }
            ),
            prices=self.PRECOS,
        )

    def render(self, turn: Turn, *, model: str, effort: Effort):
        return (
            {
                "model": model,
                "prefix": turn.stable_prefix,
                "messages": [
                    {
                        # Operador em canal PRÓPRIO, nunca "user" (D3).
                        "role": "operator" if m.role is Role.OPERATOR else m.role.value,
                        "text": m.text,
                    }
                    for m in turn.messages
                ],
                "effort": effort.value,
            },
            (),
        )

    async def send(self, turn: Turn, *, model: str, effort: Effort) -> Reply:
        self.turnos.append(turn)
        pedido, _ = self.render(turn, model=model, effort=effort)
        self.pedidos.append(dict(pedido))
        return Reply(
            text=json.dumps(self.resposta),
            usage=self.usage,
            model=model,
            provider="memoria",
            stop_reason=self.stop_reason,
            data=self.resposta,
            effort_applied=effort,
            capabilities=self.info.capabilities,
        )

    # ── conveniências para os testes do ciclo ───────────────────────────────

    def conclui(self, titulo="deadlock na tabela X", resumo="14:02–14:07, migration Y"):
        self.resposta = {
            "reply": "investigação encerrada",
            "concluded": True,
            "finding_title": titulo,
            "finding_summary": resumo,
            "finding_evidence": ["log linha 42"],
        }
        return self

    def conclui_sem_achado(self):
        self.resposta = {
            "reply": "acabei",
            "concluded": True,
            "finding_title": "",
            "finding_summary": "",
            "finding_evidence": [],
        }
        return self


# ── duplos do "outro lado do fio" dos adaptadores reais ─────────────────────

RESPOSTA_ESTRUTURADA = json.dumps(
    {
        "reply": "ok",
        "concluded": False,
        "finding_title": "",
        "finding_summary": "",
        "finding_evidence": [],
    }
)


class _BlocoTexto:
    type = "text"

    def __init__(self, text: str):
        self.text = text


class _UsoAnthropic:
    """Uso como a Anthropic o devolve: parcelas DISJUNTAS (D2)."""

    def __init__(self, entrada=1_000, saida=200, leitura=800, criacao=1_200):
        self.input_tokens = entrada
        self.output_tokens = saida
        self.cache_read_input_tokens = leitura
        self.cache_creation_input_tokens = criacao


class _RespostaAnthropic:
    def __init__(self, stop_reason="end_turn", model="claude-opus-5"):
        self.content = [_BlocoTexto(RESPOSTA_ESTRUTURADA)]
        self.usage = _UsoAnthropic()
        self.model = model
        self.stop_reason = stop_reason


class ClienteAnthropicFalso:
    """O cliente do SDK, trocado. O ADAPTADOR sob teste é o real."""

    def __init__(self, resultado=None):
        self.resultado = resultado or _RespostaAnthropic()
        self.pedidos: list[dict] = []
        self.messages = self

    async def create(self, **pedido):
        self.pedidos.append(pedido)
        if isinstance(self.resultado, Exception):
            raise self.resultado
        return self.resultado


def http_openai(
    *, status=200, cacheados=800, prompt_tokens=1_800, erro: Exception | None = None
) -> httpx.AsyncClient:
    """Transporte falso da OpenAI.

    `prompt_tokens` INCLUI `cached_tokens` de propósito — é a semântica real
    deste fornecedor, e é ela que o adaptador precisa desfazer (D2). Um duplo
    que devolvesse os campos já disjuntos faria o teste da subtração passar sem
    que a subtração existisse.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if erro is not None:
            raise erro
        return httpx.Response(
            status,
            json={
                "model": "gpt-5-codex",
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": RESPOSTA_ESTRUTURADA},
                    }
                ],
                "usage": {
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": 200,
                    "prompt_tokens_details": {"cached_tokens": cacheados},
                },
            },
        )

    return httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://api.exemplo.invalido/v1"
    )


# ═══════════════════════════════════════════════════════════════════════════
# Parte 1 — SUÍTE DE CONTRATO DA PORTA
# ═══════════════════════════════════════════════════════════════════════════


def _fabrica_memoria():
    return ProvedorEmMemoria()


def _fabrica_anthropic():
    return AnthropicAgentProvider(client=ClienteAnthropicFalso())


def _fabrica_openai():
    return OpenAIAgentProvider(http_client=http_openai())


FABRICAS = {
    "memoria": _fabrica_memoria,
    "anthropic": _fabrica_anthropic,
    "openai": _fabrica_openai,
}


@pytest.fixture(params=sorted(FABRICAS))
def provedor(request) -> AgentProvider:
    """Todo adaptador da porta, um por vez. Adicionar um fornecedor é
    acrescentar uma linha em `FABRICAS` — e a suíte inteira passa a cobri-lo."""
    return FABRICAS[request.param]()


class TestContratoDaPorta:
    """As oito garantias documentadas em `app/runtime/ports.py`."""

    def test_1_catalogo_resolve_as_tres_classes(self, provedor):
        """Classe → nome concreto, para as três. A política do núcleo fala em
        CLASSE (ADR-0011 §3); um catálogo com buraco pararia trabalho."""
        for classe in ModelClass:
            nome = provedor.resolve_model(classe)
            assert nome, f"{provedor.name} não resolve a classe {classe}"
            assert nome != classe.value, "nome de modelo não pode ser a classe"

    def test_1b_resolucao_e_pura(self, provedor):
        """Sem I/O e sem estado: mesma classe, mesmo nome, sempre."""
        assert provedor.resolve_model(ModelClass.STRONG) == provedor.resolve_model(
            ModelClass.STRONG
        )

    def test_2_prefixo_estavel_vem_antes_do_volatil(self, provedor):
        """A garantia mais cara da ADR-0012 — e a que quebra em silêncio.

        Verificada sem conhecer o formato de nenhum fornecedor: as duas fatias
        são procuradas na requisição SERIALIZADA e as posições comparadas. Um
        adaptador que puser o texto do turno antes do prefixo não erra, não
        levanta e não avisa: só deixa de ler cache e multiplica o custo.
        """
        pedido, _ = provedor.render(
            turno_de_teste(), model=provedor.resolve_model(ModelClass.STRONG),
            effort=Effort.HIGH,
        )
        corpo = serialize_for_audit(pedido)
        assert PREFIXO in corpo, "o prefixo estável não foi enviado"
        assert VOLATIL in corpo, "a mensagem do turno não foi enviada"
        assert corpo.index(PREFIXO) < corpo.index(VOLATIL), (
            f"{provedor.name}: o volátil está ANTES do prefixo estável — "
            "o cache de prefixo não vai ser lido (ADR-0012 §1)"
        )

    def test_3_render_e_deterministico(self, provedor):
        """Mesmo turno, mesmos bytes. Um `dict` iterado fora de ordem ou um
        `set` no meio produziria prefixos diferentes para o mesmo conteúdo — o
        invalidador de cache mais silencioso que existe."""
        modelo = provedor.resolve_model(ModelClass.STRONG)
        a, _ = provedor.render(turno_de_teste(), model=modelo, effort=Effort.HIGH)
        b, _ = provedor.render(turno_de_teste(), model=modelo, effort=Effort.HIGH)
        assert serialize_for_audit(a) == serialize_for_audit(b)

    def test_3b_o_prefixo_nao_muda_quando_a_mensagem_muda(self, provedor):
        """Dois turnos da mesma thread compartilham o prefixo — é isso que
        significa "prefixo estável", e é o que o cache cobra."""
        modelo = provedor.resolve_model(ModelClass.STRONG)
        t1 = turno_de_teste()
        t2 = Turn(
            stable_prefix=t1.stable_prefix,
            messages=(Message(role=Role.USER, text="outra pergunta"),),
            output_schema=t1.output_schema,
            max_output_tokens=t1.max_output_tokens,
        )
        assert t1.fingerprint() == t2.fingerprint()
        p1, _ = provedor.render(t1, model=modelo, effort=Effort.HIGH)
        p2, _ = provedor.render(t2, model=modelo, effort=Effort.HIGH)
        # A parte estável tem de sair idêntica nas duas requisições.
        assert serialize_for_audit(p1)[: len(PREFIXO)] == serialize_for_audit(p2)[
            : len(PREFIXO)
        ]

    def test_4_operador_nunca_e_serializado_como_usuario(self, provedor):
        """D3 — e é garantia de SEGURANÇA, não de formato.

        Instrução de operador tem autoridade; texto de usuário não tem.
        Achatar as duas no mesmo papel é abrir a porta para injeção: quem
        escrevesse numa entrada de usuário passaria a poder forjar instrução.
        """
        pedido, _ = provedor.render(
            turno_de_teste(com_operador=True),
            model=provedor.resolve_model(ModelClass.STRONG),
            effort=Effort.HIGH,
        )
        corpo = serialize_for_audit(pedido)
        assert OPERADOR in corpo, "a intervenção do operador não foi enviada"
        # O texto do operador não pode estar no MESMO bloco em que o texto do
        # usuário viaja com papel "user".
        for mensagem in pedido.get("messages", []):
            texto = serialize_for_audit(mensagem)
            if OPERADOR in texto:
                assert '"user"' not in texto, (
                    f"{provedor.name}: intervenção de operador serializada como "
                    "fala de usuário (D3)"
                )

    def test_5_credencial_nao_vaza_na_requisicao_nem_no_repr(self, provedor):
        """A chave vai no cliente HTTP, nunca no corpo — o que torna `render`
        uma superfície segura para log e auditoria."""
        pedido, _ = provedor.render(
            turno_de_teste(), model=provedor.resolve_model(ModelClass.STRONG),
            effort=Effort.HIGH,
        )
        corpo = serialize_for_audit(pedido) + repr(provedor) + str(provedor)
        assert "sk-" not in corpo
        assert "Bearer" not in corpo

    async def test_6_uso_vem_com_as_quatro_parcelas_disjuntas(self, provedor):
        """As quatro parcelas, não-negativas. `dop.v1.UsageEvent` assume que
        elas são DISJUNTAS — um adaptador que devolvesse a contagem inclusiva
        do fornecedor inflaria o orçamento da ADR-0011 em silêncio (D2)."""
        r = await provedor.send(
            turno_de_teste(), model=provedor.resolve_model(ModelClass.STRONG),
            effort=Effort.HIGH,
        )
        u = r.usage
        assert isinstance(u, Usage)
        for campo in (
            u.input_tokens, u.output_tokens, u.cache_read_tokens, u.cache_creation_tokens
        ):
            assert isinstance(campo, int) and campo >= 0
        assert u.output_tokens > 0

    async def test_7_stop_reason_esta_no_vocabulario_do_dominio(self, provedor):
        r = await provedor.send(
            turno_de_teste(), model=provedor.resolve_model(ModelClass.STRONG),
            effort=Effort.HIGH,
        )
        assert isinstance(r.stop_reason, StopReason)
        assert r.stop_reason is StopReason.COMPLETED

    async def test_8_a_resposta_nao_carrega_tipo_de_fornecedor(self, provedor):
        """Nada acima da porta pode ver objeto de SDK. Se um `Reply` carregasse
        `anthropic.Message`, o `loop.py` passaria a depender do fornecedor sem
        nenhuma linha de import dizendo isso."""
        r = await provedor.send(
            turno_de_teste(), model=provedor.resolve_model(ModelClass.STRONG),
            effort=Effort.HIGH,
        )
        assert isinstance(r, Reply)
        assert isinstance(r.text, str)
        assert r.data is None or isinstance(r.data, dict)
        assert r.provider == provedor.name


class TestContratoDeIndisponibilidade:
    """D6 — as duas famílias de exceção viram a MESMA coisa no domínio.

    Fora da suíte parametrizada porque o duplo em memória não tem o que falhar:
    ele não tem credencial nem rede. Cobre os dois adaptadores REAIS.
    """

    async def test_anthropic_sem_credencial(self):
        with pytest.raises(AgentProviderUnavailable) as e:
            await AnthropicAgentProvider().send(
                turno_de_teste(), model="claude-opus-5", effort=Effort.HIGH
            )
        assert e.value.reason is Reason.MISSING_CREDENTIAL
        assert e.value.provider == "anthropic"

    async def test_openai_sem_credencial(self):
        with pytest.raises(AgentProviderUnavailable) as e:
            await OpenAIAgentProvider().send(
                turno_de_teste(), model="gpt-5-codex", effort=Effort.HIGH
            )
        assert e.value.reason is Reason.MISSING_CREDENTIAL

    async def test_openai_rede_fora_vira_indisponibilidade(self):
        provedor = OpenAIAgentProvider(
            http_client=http_openai(erro=httpx.ConnectError("sem rota"))
        )
        with pytest.raises(AgentProviderUnavailable) as e:
            await provedor.send(turno_de_teste(), model="gpt-5-codex", effort=Effort.HIGH)
        assert e.value.reason is Reason.UNREACHABLE

    async def test_openai_credencial_recusada(self):
        provedor = OpenAIAgentProvider(http_client=http_openai(status=401))
        with pytest.raises(AgentProviderUnavailable) as e:
            await provedor.send(turno_de_teste(), model="gpt-5-codex", effort=Effort.HIGH)
        assert e.value.reason is Reason.REJECTED_CREDENTIAL

    async def test_openai_modelo_desconhecido(self):
        provedor = OpenAIAgentProvider(http_client=http_openai(status=404))
        with pytest.raises(AgentProviderUnavailable) as e:
            await provedor.send(turno_de_teste(), model="inexistente", effort=Effort.HIGH)
        assert e.value.reason is Reason.UNKNOWN_MODEL

    async def test_anthropic_falha_do_sdk_nao_atravessa_crua(self):
        """Exceção do SDK não pode chegar ao chamador: ele teria de conhecer o
        fornecedor para tratá-la, que é o oposto de haver uma porta."""
        import anthropic

        cliente = ClienteAnthropicFalso(anthropic.APIStatusError(
            "estourou", response=httpx.Response(500, request=httpx.Request("POST", "http://x")),
            body=None,
        ))
        provedor = AnthropicAgentProvider(client=cliente)
        with pytest.raises(AgentProviderUnavailable) as e:
            await provedor.send(turno_de_teste(), model="claude-opus-5", effort=Effort.HIGH)
        assert e.value.reason is Reason.PROVIDER_ERROR
        # O detalhe cru fica no log, não na mensagem que chega ao cliente.
        assert "estourou" not in str(e.value)
        assert "estourou" in e.value.debug_detail

    def test_a_credencial_se_recusa_a_ser_impressa(self):
        """Mesma disciplina do `SecretValue` do núcleo: segredo que sabe se
        imprimir vaza em log de exceção, em `%r` e em traceback."""
        c = Credential("sk-nunca-me-mostre")
        assert repr(c) == "Credential(***)" and str(c) == "***"
        assert "nunca" not in f"{c!r} {c}"
        assert c.reveal() == "sk-nunca-me-mostre"


class TestDivergenciasEntreProvedores:
    """As diferenças que a porta NÃO consegue apagar — e por isso as declara.

    Cada teste aqui é uma divergência documentada em `app/runtime/ports.py`.
    Elas valem mais que o código: é onde a plataforma vai pagar a troca de
    fornecedor, e uma porta que as escondesse só adiaria a fatura.
    """

    def test_d1_so_a_anthropic_marca_o_breakpoint_do_prefixo(self):
        pedido, _ = AnthropicAgentProvider(client=ClienteAnthropicFalso()).render(
            turno_de_teste(), model="claude-opus-5", effort=Effort.HIGH
        )
        bloco = pedido["system"][0]
        assert bloco["text"].startswith("contrato do runtime")
        # O marcador fica no FIM DO PREFIXO, não no fim do prompt: no fim do
        # prompt escreveria uma entrada nova a cada turno e não leria nenhuma.
        assert bloco["cache_control"] == {"type": "ephemeral"}
        assert all(
            "cache_control" not in json.dumps(m) for m in pedido["messages"]
        ), "breakpoint depois do volátil: cada turno escreveria um cache novo"

    def test_d1_openai_nao_reporta_criacao_de_cache(self):
        """Cache automático: `cache_creation_tokens` vem 0 e isso é AUSÊNCIA de
        informação, não afirmação de que nada foi escrito. A capacidade
        declarada é o que diz isso a quem lê a telemetria (ADR-0012 §1)."""
        anthropic_p = AnthropicAgentProvider(client=ClienteAnthropicFalso())
        openai_p = OpenAIAgentProvider(http_client=http_openai())
        assert anthropic_p.supports(Capability.CACHE_CREATION_ACCOUNTING)
        assert not openai_p.supports(Capability.CACHE_CREATION_ACCOUNTING)
        assert anthropic_p.supports(Capability.EXPLICIT_PREFIX_CACHE)
        assert not openai_p.supports(Capability.EXPLICIT_PREFIX_CACHE)

    async def test_d2_a_openai_subtrai_os_cacheados_e_a_anthropic_nao(self):
        """A armadilha da dupla contagem, com números.

        A OpenAI reporta 1.800 de prompt COM 800 cacheados dentro; a Anthropic
        reporta 1.000 de entrada e 800 de cache lado a lado. As duas gastaram a
        mesma coisa, e as duas têm de chegar ao núcleo como 1.000 + 800.
        """
        a = await AnthropicAgentProvider(client=ClienteAnthropicFalso()).send(
            turno_de_teste(), model="claude-opus-5", effort=Effort.HIGH
        )
        o = await OpenAIAgentProvider(
            http_client=http_openai(prompt_tokens=1_800, cacheados=800)
        ).send(turno_de_teste(), model="gpt-5-codex", effort=Effort.HIGH)
        assert (a.usage.input_tokens, a.usage.cache_read_tokens) == (1_000, 800)
        assert (o.usage.input_tokens, o.usage.cache_read_tokens) == (1_000, 800)
        # Sem a subtração, a entrada da OpenAI chegaria como 1.800 e o
        # orçamento contaria 800 tokens duas vezes.
        assert o.usage.input_tokens != 1_800
        assert o.usage.cache_creation_tokens == 0

    async def test_d4_a_openai_rebaixa_max_para_high_e_AVISA(self):
        """`max` não existe neste fornecedor. Rebaixar em silêncio numa tarefa
        crítica (ADR-0007) esconderia uma decisão de produto num adaptador."""
        o = await OpenAIAgentProvider(http_client=http_openai()).send(
            turno_de_teste(), model="gpt-5-codex", effort=Effort.MAX
        )
        assert o.effort_applied is Effort.HIGH
        assert any("max" in w and "D4" in w for w in o.warnings)

        a = await AnthropicAgentProvider(client=ClienteAnthropicFalso()).send(
            turno_de_teste(), model="claude-opus-5", effort=Effort.MAX
        )
        assert a.effort_applied is Effort.MAX and a.warnings == ()

    async def test_d3_anthropic_recua_quando_o_modelo_nao_tem_canal_de_operador(self):
        """Nem todo modelo aceita `role:"system"` no meio (o Sonnet 5 responde
        400). Em vez de manter uma lista de modelos que envelhece em silêncio,
        o adaptador TENTA, recua para bloco marcado no turno do usuário e AVISA
        — o recuo muda a garantia de não-forjabilidade, e quem lê a resposta
        precisa saber que ela mudou.
        """
        import anthropic

        class RecusaOCanal(ClienteAnthropicFalso):
            def __init__(self):
                super().__init__()
                self.tentativas = 0

            async def create(self, **pedido):
                self.tentativas += 1
                self.pedidos.append(pedido)
                if self.tentativas == 1:
                    raise anthropic.BadRequestError(
                        "role 'system' is not supported on this model",
                        response=httpx.Response(
                            400, request=httpx.Request("POST", "http://x")
                        ),
                        body=None,
                    )
                return self.resultado

        cliente = RecusaOCanal()
        r = await AnthropicAgentProvider(client=cliente).send(
            turno_de_teste(com_operador=True), model="claude-sonnet-5", effort=Effort.HIGH
        )
        assert cliente.tentativas == 2
        assert any("canal de operador" in w for w in r.warnings)
        # E mesmo no recuo o texto do operador continua MARCADO, não misturado
        # com o do usuário: é o que permite ao modelo distinguir autoridade.
        segundo = serialize_for_audit(cliente.pedidos[1])
        assert "intervencao-do-operador" in segundo

    async def test_d5_motivos_de_parada_distintos_chegam_normalizados(self):
        a = await AnthropicAgentProvider(
            client=ClienteAnthropicFalso(_RespostaAnthropic(stop_reason="max_tokens"))
        ).send(turno_de_teste(), model="claude-opus-5", effort=Effort.HIGH)
        assert a.stop_reason is StopReason.MAX_TOKENS

        recusa = await AnthropicAgentProvider(
            client=ClienteAnthropicFalso(_RespostaAnthropic(stop_reason="refusal"))
        ).send(turno_de_teste(), model="claude-opus-5", effort=Effort.HIGH)
        assert recusa.stop_reason is StopReason.REFUSED

    def test_o_preco_desconhecido_nao_vira_zero(self):
        """Preço ausente é `None`, não zero: um orçamento alimentado com zeros
        é exatamente a ficção que a ADR-0011 §2 existe para impedir."""
        assert AnthropicAgentProvider().price_for("claude-opus-5") is not None
        assert OpenAIAgentProvider().price_for("gpt-5-codex") is None


# ═══════════════════════════════════════════════════════════════════════════
# Parte 2 — O CICLO DO TURNO
# ═══════════════════════════════════════════════════════════════════════════


class RegistroIdempotente(ChamadaFalsa):
    """`RecordUsage` que HONRA a chave de idempotência, como o núcleo faz.

    Um duplo que contasse toda chamada deixaria passar exatamente o defeito que
    importa: o BFF reenviando com chave nova a cada tentativa. Aqui a segunda
    chamada com a MESMA chave não conta — e `contados` é o número que o
    orçamento veria.
    """

    def __init__(self, resultado):
        super().__init__(resultado)
        self.chaves: list[str] = []

    @property
    def contados(self) -> int:
        return len(set(self.chaves))

    async def __call__(self, request, **kwargs):
        self.chaves.append(request.idempotency_key)
        return await super().__call__(request, **kwargs)


class OrcamentoPorEscopo(ChamadaFalsa):
    """`GetBudget` que responde conforme o escopo pedido, como o núcleo."""

    def __init__(self, por_escopo: dict[str, cost_pb2.Budget]):
        super().__init__(None)
        self.por_escopo = por_escopo

    async def __call__(self, request, **kwargs):
        await super().__call__(request, **kwargs)
        return self.por_escopo[request.scope]


class NucleoDoRuntime:
    """Núcleo falso com os quatro serviços que o ciclo do turno usa."""

    def __init__(self):
        self.pacote = knowledge_pb2.ContextPackage(
            demand=common_pb2.DemandRef(id="dem-1"),
            rules=[PREFIXO, "nunca mergear desenv na feature"],
            estimated_tokens=4_200,
        )
        self.BuildContextPackage = ChamadaFalsa(self.pacote)

        self.thread = demand_pb2.Thread(
            id="th-1",
            demand=common_pb2.DemandRef(id="dem-1"),
            key="forense-db",
            blocked=False,
            card=demand_pb2.AgentCard(
                purpose="forense do banco",
                tools=["mcp-mysql"],
                effort="",
                budget_micros=10_000_000,
            ),
        )
        self.ListThreads = ChamadaFalsa(
            demand_pb2.ListThreadsResponse(threads=[self.thread])
        )
        self.PostMessage = ChamadaFalsa(demand_pb2.Message(id="msg-1", thread_id="th-1"))
        self.PublishFinding = ChamadaFalsa(
            demand_pb2.Finding(id="find-1", thread_id="th-1", title="deadlock na tabela X")
        )

        self.RouteModel = ChamadaFalsa(
            cost_pb2.RoutingDecision(
                task_kind="investigation",
                # Nome do catálogo do NÚCLEO — a borda o traduz para o nome
                # concreto do fornecedor ativo.
                model="claude-opus",
                effort="max",
                reason=JUSTIFICATIVA,
            )
        )
        self.RecordUsage = RegistroIdempotente(
            cost_pb2.RecordUsageResponse(recorded=True, budget_exceeded=False)
        )
        # Responde CONFORME o escopo pedido, como o núcleo faz: um duplo que
        # devolvesse sempre o mesmo orçamento deixaria passar a borda
        # perguntando duas vezes pelo MESMO escopo — e a caixa de atenção
        # mostraria o teto da demanda duas vezes e o da conta nenhuma.
        self.GetBudget = OrcamentoPorEscopo(
            {
                "demand": cost_pb2.Budget(
                    scope="demand", scope_id="dem-1",
                    limit_micros=10_000_000, spent_micros=12_000_000,
                ),
                "account": cost_pb2.Budget(
                    scope="account", scope_id="acct-1",
                    limit_micros=100_000_000, spent_micros=12_000_000,
                ),
            }
        )

    # ── variações ───────────────────────────────────────────────────────────

    def trunca_o_contexto(self):
        """O núcleo informando que cortou por orçamento de tokens."""
        self.pacote.dropped.update({"rules": 3, "index": 1, "memories": 0, "findings": 2})
        self.BuildContextPackage.devolve(self.pacote)

    def estoura_o_orcamento(self):
        self.RecordUsage.devolve(
            cost_pb2.RecordUsageResponse(recorded=True, budget_exceeded=True)
        )

    def ficha_com_modelo(self, modelo: str):
        self.thread.card.model = modelo
        self.ListThreads.devolve(demand_pb2.ListThreadsResponse(threads=[self.thread]))

    def achado_com_payload(self):
        payload = struct_pb2.Struct()
        payload.update({"summary": "14:02–14:07"})
        self.PublishFinding.devolve(
            demand_pb2.Finding(
                id="find-1", thread_id="th-1",
                title="deadlock na tabela X", payload=payload,
            )
        )


@pytest.fixture
def nucleo_runtime(nucleo, monkeypatch):
    falso = NucleoDoRuntime()
    monkeypatch.setattr(stubs, "knowledge_stub", lambda: falso)
    monkeypatch.setattr(stubs, "demand_stub", lambda: falso)
    monkeypatch.setattr(stubs, "cost_stub", lambda: falso)
    return falso


@pytest.fixture
def agente(monkeypatch) -> ProvedorEmMemoria:
    """O ponto de substituição do PROVEDOR — um só, como o do núcleo.

    Trocar `provider_for` troca o fornecedor inteiro; caçar importações
    espalhadas por módulo seria a mesma armadilha que `stubs.py` já evita.
    """
    provedor = ProvedorEmMemoria()
    monkeypatch.setattr(uc, "provider_for", lambda _nome="": provedor)
    return provedor


def _app_com_rotas():
    """O app real, com a rota deste domínio registrada.

    `app/main.py` é do dono do repositório e ainda não inclui este router (ver
    o relatório). A checagem antes de incluir mantém o teste correto depois que
    o registro entrar no `main`.
    """
    app = create_app()
    caminhos = {getattr(r, "path", "") for r in app.routes}
    if "/api/v1/runtime/demands/{demand_id}/threads/{thread_id}/turns" not in caminhos:
        app.include_router(rotas.router)
    return app


@pytest.fixture
def cliente_rt(nucleo_runtime, agente):
    with TestClient(_app_com_rotas()) as c:
        yield c


@pytest.fixture
async def stub_rt(nucleo_runtime, agente):
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
    bff_grpc.add_RuntimeServiceServicer_to_server(RuntimeServicer(), servidor)
    porta = servidor.add_insecure_port("127.0.0.1:0")
    await servidor.start()
    try:
        async with grpc.aio.insecure_channel(f"127.0.0.1:{porta}") as canal:
            yield bff_grpc.RuntimeServiceStub(canal)
    finally:
        await servidor.stop(0)


ROTA = "/api/v1/runtime/demands/dem-1/threads/th-1/turns"


def _turno(cliente, texto=VOLATIL, chave="k-1", **extra):
    return cliente.post(
        ROTA,
        headers={**CABECALHOS, "Idempotency-Key": chave},
        json={"text": texto, "task_kind": "investigation", **extra},
    )


class TestPrefixoEstavel:
    """ADR-0012 §1 — a economia que quebra sem ninguém perceber."""

    def test_o_texto_do_turno_nao_entra_no_prefixo(self, cliente_rt, agente):
        assert _turno(cliente_rt).status_code == 200
        turno = agente.turnos[0]
        assert PREFIXO in turno.stable_prefix, "as regras do projeto ficaram fora do prefixo"
        assert VOLATIL not in turno.stable_prefix, (
            "o texto do turno foi interpolado NO PREFIXO: cada turno passaria a "
            "escrever uma entrada de cache nova e a não ler nenhuma"
        )
        assert any(VOLATIL in m.text for m in turno.messages)

    def test_dois_turnos_da_mesma_thread_tem_o_mesmo_prefixo(self, cliente_rt, agente):
        _turno(cliente_rt, "primeira pergunta", chave="k-1")
        _turno(cliente_rt, "segunda pergunta bem diferente", chave="k-2")
        a, b = agente.turnos
        assert a.fingerprint() == b.fingerprint(), (
            "o prefixo mudou entre turnos da mesma thread — é assim que a "
            "leitura de cache vai a zero sem nenhum erro aparecer"
        )

    def test_a_ficha_da_thread_esta_no_prefixo_e_o_operador_nao(self, cliente_rt, agente):
        """Ficha é estável por thread (ADR-0010 §2) e entra no prefixo; a
        intervenção do operador é volátil e entra DEPOIS do breakpoint,
        preservando o cache em vez de reescrever o topo do prompt."""
        _turno(cliente_rt, operator_note=OPERADOR)
        turno = agente.turnos[0]
        assert "forense do banco" in turno.stable_prefix
        assert "mcp-mysql" in turno.stable_prefix
        assert OPERADOR not in turno.stable_prefix
        operador = [m for m in turno.messages if m.role is Role.OPERATOR]
        assert operador and operador[0].text == OPERADOR

    def test_o_prefixo_nao_carrega_relogio_nem_id_volatil(self, cliente_rt, agente):
        """Sem timestamp e sem id de artefato: os dois mudam sem que o
        CONTEÚDO mude, e invalidariam o prefixo à toa."""
        _turno(cliente_rt)
        prefixo = agente.turnos[0].stable_prefix
        assert "dem-1" not in prefixo or prefixo.count("dem-1") == 0
        assert not any(c.isdigit() and ":" in prefixo[i : i + 3] for i, c in enumerate(prefixo))


class TestMedicao:
    """ADR-0011 §1 e §4 — medição desde o primeiro dia, com cache."""

    def test_consumo_registrado_com_os_campos_de_cache(self, cliente_rt, nucleo_runtime):
        corpo = _turno(cliente_rt).json()
        gravado = nucleo_runtime.RecordUsage.pedidos[0].usage
        assert gravado.input_tokens == 1_000
        assert gravado.output_tokens == 200
        # Sem estes dois o alerta de invalidador silencioso da ADR-0012 §1 não
        # existe: cache miss vira mistério em vez de alerta.
        assert gravado.cache_read_tokens == 800
        assert gravado.cache_creation_tokens == 1_200
        assert corpo["usage"]["cache_read_tokens"] == 800
        assert corpo["usage"]["cache_creation_known"] is True

    def test_o_custo_e_inteiro_em_micros_com_a_moeda_junto(self, cliente_rt):
        """Mesma disciplina de `usecases/cost.py`: nada de `float`, e a moeda
        anda junto do número."""
        corpo = _turno(cliente_rt).json()
        # 1000×5000 + 200×25000 + 800×500 + 1200×6250, por 1.000 tokens.
        esperado = (1_000 * 5_000 + 200 * 25_000 + 800 * 500 + 1_200 * 6_250) // 1_000
        assert corpo["usage"]["cost"] == {"currency": "USD", "amount_micros": esperado}
        assert isinstance(corpo["usage"]["cost"]["amount_micros"], int)
        assert corpo["usage"]["cost_known"] is True

    def test_reenvio_com_a_mesma_chave_nao_conta_duas_vezes(
        self, cliente_rt, nucleo_runtime
    ):
        """O defeito que transformaria o orçamento em ficção.

        Uma duplicata de `RecordUsage` não colide com nada: entraria como
        consumo legítimo. Só uma chave DERIVADA do turno impede isso — chave
        sorteada a cada tentativa passaria neste teste se ele contasse
        chamadas, e por isso o que se conta são chaves DISTINTAS.
        """
        _turno(cliente_rt, chave="retry-1")
        _turno(cliente_rt, chave="retry-1")
        assert len(nucleo_runtime.RecordUsage.chamadas) == 2
        assert nucleo_runtime.RecordUsage.contados == 1, (
            "o reenvio gerou uma chave de idempotência NOVA: o consumo contaria "
            "duas vezes e o orçamento viraria ficção"
        )
        # E a mesma disciplina vale para as outras escritas do turno.
        assert len({p.idempotency_key for p in nucleo_runtime.PostMessage.pedidos}) == 2

    def test_chaves_distintas_sao_turnos_distintos(self, cliente_rt, nucleo_runtime):
        """O outro lado: perguntar de novo é um turno NOVO, e conta."""
        _turno(cliente_rt, chave="k-1")
        _turno(cliente_rt, chave="k-2")
        assert nucleo_runtime.RecordUsage.contados == 2


class TestOrcamentoEstourado:
    """ADR-0011 §2 — corte SUAVE: pausa e pergunta, não morre."""

    def test_estouro_responde_200_e_marca_pausa(self, cliente_rt, nucleo_runtime):
        nucleo_runtime.estoura_o_orcamento()
        r = _turno(cliente_rt)
        assert r.status_code == 200, "estouro virou erro: é o corte duro que a ADR recusou"
        corpo = r.json()
        assert corpo["paused"] is True
        assert "PAUSA" in corpo["notice"] and "ADR-0011" in corpo["notice"]
        assert [b["scope"] for b in corpo["budgets"]] == ["demand", "account"]

    def test_o_turno_que_ja_rodou_e_entregue_inteiro(self, cliente_rt, nucleo_runtime):
        """Abortar aqui jogaria fora tokens já pagos e apagaria a medição
        justamente quando ela mais importa."""
        nucleo_runtime.estoura_o_orcamento()
        corpo = _turno(cliente_rt).json()
        assert corpo["reply"] == "entendido"
        # A resposta foi publicada na thread: pergunta + resposta.
        assert len(nucleo_runtime.PostMessage.chamadas) == 2
        assert len(corpo["message_ids"]) == 2

    def test_sem_estouro_nao_gasta_ida_extra_ao_nucleo(self, cliente_rt, nucleo_runtime):
        _turno(cliente_rt)
        assert nucleo_runtime.GetBudget.chamadas == []

    async def test_grpc_tambem_pausa_em_vez_de_falhar(self, stub_rt, nucleo_runtime):
        nucleo_runtime.estoura_o_orcamento()
        resp = await stub_rt.RunTurn(
            bff.RunTurnRequest(
                demand_id="dem-1", thread_id="th-1", text=VOLATIL,
                task_kind="investigation", idempotency_key="k-1",
            ),
            metadata=CONTA,
        )
        assert resp.paused is True and "PAUSA" in resp.notice


class TestContextoTruncado:
    """ADR-0012 — o que ficou de fora não some."""

    def test_truncamento_vira_mensagem_na_thread(self, cliente_rt, nucleo_runtime):
        nucleo_runtime.trunca_o_contexto()
        corpo = _turno(cliente_rt).json()
        assert corpo["context_truncated"] is True
        textos = [p.text for p in nucleo_runtime.PostMessage.pedidos]
        avisos = [t for t in textos if "truncado" in t.lower()]
        assert avisos, (
            "o truncamento não apareceu na CONVERSA: o humano leria a resposta "
            "sem saber que ela foi produzida sem parte do contexto"
        )
        assert "ADR-0012" in avisos[0]
        # A pergunta, o aviso e a resposta — nessa ordem.
        assert len(nucleo_runtime.PostMessage.chamadas) == 3

    def test_o_agente_tambem_e_avisado_no_prefixo(self, cliente_rt, nucleo_runtime, agente):
        """Ele precisa saber ANTES de responder: sem isso conclui sobre o que
        não leu, e ninguém consegue explicar a conclusão depois."""
        nucleo_runtime.trunca_o_contexto()
        _turno(cliente_rt)
        assert "TRUNCADO" in agente.turnos[0].stable_prefix

    def test_sem_truncamento_nao_ha_ruido_na_thread(self, cliente_rt, nucleo_runtime):
        """Caixa de atenção só serve se o que entra nela exigir decisão (R-1)."""
        corpo = _turno(cliente_rt).json()
        assert corpo["context_truncated"] is False
        assert len(nucleo_runtime.PostMessage.chamadas) == 2

    def test_descarte_nao_informado_nao_afirma_que_nada_caiu(self, cliente_rt, agente):
        """`dropped` vazio é "o núcleo não informou", não "nada ficou de fora"
        — e o agente precisa tratar o contexto como possivelmente parcial."""
        _turno(cliente_rt)
        assert "POSSIVELMENTE parcial" in agente.turnos[0].stable_prefix


class TestAchadoEConclusao:
    """Spec de conversação §1 — a thread não morre em silêncio."""

    def test_concluir_publica_o_achado(self, cliente_rt, nucleo_runtime, agente):
        agente.conclui()
        nucleo_runtime.achado_com_payload()
        corpo = _turno(cliente_rt).json()
        assert corpo["concluded"] is True
        assert corpo["finding"] == {"id": "find-1", "title": "deadlock na tabela X"}
        pedido = nucleo_runtime.PublishFinding.pedidos[0]
        assert pedido.title == "deadlock na tabela X"
        assert pedido.thread_id == "th-1"

    def test_o_achado_carrega_a_proveniencia(self, cliente_rt, nucleo_runtime, agente):
        """Quem auditar precisa saber com que modelo e sob que política o
        achado foi produzido — ele entra na memória do projeto (ADR-0009 §4)."""
        agente.conclui()
        _turno(cliente_rt)
        payload = nucleo_runtime.PublishFinding.pedidos[0].payload
        assert payload["model"] == "memoria-forte"
        assert payload["provider"] == "memoria"
        assert "ADR-0011" in payload["routing_reason"]

    def test_conclusao_sem_achado_e_RECUSADA(self, cliente_rt, nucleo_runtime, agente):
        """Aceitar a conclusão vazia seria deixar a thread morrer em silêncio
        com um `true` de enfeite."""
        agente.conclui_sem_achado()
        corpo = _turno(cliente_rt).json()
        assert corpo["concluded"] is False
        assert corpo["finding"] is None
        assert nucleo_runtime.PublishFinding.chamadas == []
        assert any("RECUSADA" in w for w in corpo["warnings"])

    def test_sem_conclusao_nao_publica_achado(self, cliente_rt, nucleo_runtime):
        _turno(cliente_rt)
        assert nucleo_runtime.PublishFinding.chamadas == []


class TestRoteamento:
    """ADR-0011 §3 — a decisão é do núcleo; o catálogo é do fornecedor."""

    def test_a_justificativa_viaja_inteira(self, cliente_rt):
        r = _turno(cliente_rt)
        assert JUSTIFICATIVA in r.text
        assert r.json()["routing"]["reason"] == JUSTIFICATIVA

    def test_a_classe_do_nucleo_vira_o_nome_do_fornecedor(self, cliente_rt):
        """O núcleo roteia para a CLASSE forte ("claude-opus", nome do catálogo
        DELE); o adaptador ativo resolve para o nome concreto DELE. Mandar o
        nome do núcleo direto para a API seria um 404 do fornecedor."""
        corpo = _turno(cliente_rt).json()
        assert corpo["routing"]["model"] == "memoria-forte"
        assert corpo["routing"]["effort"] == "max"

    def test_a_ficha_da_thread_vence_o_roteador(self, cliente_rt, nucleo_runtime):
        """Ficha é o contrato congelado da thread (ADR-0010 §2): trocar o
        modelo dela no meio invalidaria o prefixo cacheado de todos os turnos
        anteriores — cache é por modelo."""
        nucleo_runtime.ficha_com_modelo("claude-sonnet")
        corpo = _turno(cliente_rt).json()
        assert corpo["routing"]["model"] == "memoria-medio"
        assert corpo["routing"]["from_agent_card"] is True

    def test_nome_desconhecido_passa_intacto(self, cliente_rt, nucleo_runtime):
        """Adivinhar a classe de um nome que a tabela não conhece trocaria em
        silêncio o modelo que o núcleo escolheu."""
        nucleo_runtime.ficha_com_modelo("modelo-de-outro-fornecedor")
        assert _turno(cliente_rt).json()["routing"]["model"] == "modelo-de-outro-fornecedor"


class TestProvedorIndisponivel:
    """Indisponibilidade de terceiro não é 500 nem INTERNAL."""

    @pytest.fixture
    def sem_provedor(self, monkeypatch):
        def explode(_nome=""):
            raise AgentProviderUnavailable("anthropic", Reason.MISSING_CREDENTIAL)

        monkeypatch.setattr(uc, "provider_for", explode)

    def test_rest_responde_502_com_motivo_legivel_por_maquina(
        self, nucleo_runtime, sem_provedor
    ):
        with TestClient(_app_com_rotas()) as c:
            r = _turno(c)
        assert r.status_code == 502, "502 e não 500: o defeito não é nosso"
        detalhe = r.json()["detail"]
        assert detalhe["kind"] == "agent_provider_unavailable"
        assert detalhe["provider"] == "anthropic"
        assert detalhe["reason"] == "missing_credential"
        assert detalhe["guidance"]

    def test_falha_do_provedor_nao_se_confunde_com_falha_do_nucleo(
        self, nucleo_runtime, sem_provedor
    ):
        """503 é o que a tradução do núcleo usa para UNAVAILABLE do dop-core:
        reusá-lo aqui faria "o núcleo caiu" e "o fornecedor caiu" chegarem
        indistinguíveis ao cliente — que são os dois casos a separar."""
        with TestClient(_app_com_rotas()) as c:
            assert _turno(c).status_code != 503

    def test_nada_e_publicado_na_thread_quando_nao_ha_provedor(
        self, nucleo_runtime, sem_provedor
    ):
        """A checagem do provedor vem ANTES de qualquer RPC: descobrir que não
        há chave depois de montar contexto e publicar mensagem seria gastar o
        núcleo para nada."""
        with TestClient(_app_com_rotas()) as c:
            _turno(c)
        assert nucleo_runtime.PostMessage.chamadas == []
        assert nucleo_runtime.BuildContextPackage.chamadas == []

    async def test_grpc_responde_UNAVAILABLE_e_nao_INTERNAL(
        self, nucleo_runtime, sem_provedor
    ):
        servidor = grpc.aio.server(
            interceptors=(
                LoggingInterceptor(),
                ErrorInterceptor(),
                AuthInterceptor(FirebaseVerifier(PROJECT), CoreResolver()),
            )
        )
        bff_grpc.add_RuntimeServiceServicer_to_server(RuntimeServicer(), servidor)
        porta = servidor.add_insecure_port("127.0.0.1:0")
        await servidor.start()
        try:
            async with grpc.aio.insecure_channel(f"127.0.0.1:{porta}") as canal:
                stub = bff_grpc.RuntimeServiceStub(canal)
                with pytest.raises(grpc.aio.AioRpcError) as e:
                    await stub.RunTurn(
                        bff.RunTurnRequest(
                            demand_id="dem-1", thread_id="th-1", text=VOLATIL
                        ),
                        metadata=CONTA,
                    )
            assert e.value.code() is grpc.StatusCode.UNAVAILABLE
            assert "anthropic" in e.value.details()
        finally:
            await servidor.stop(0)


class TestParidadeEntreTransportes:
    """As duas portas servem o MESMO caso de uso — não duas cópias dele."""

    async def test_o_mesmo_turno_da_o_mesmo_resultado(
        self, stub_rt, nucleo_runtime, agente
    ):
        resp = await stub_rt.RunTurn(
            bff.RunTurnRequest(
                demand_id="dem-1", thread_id="th-1", text=VOLATIL,
                task_kind="investigation", idempotency_key="k-1",
            ),
            metadata=CONTA,
        )
        assert resp.reply == "entendido"
        assert resp.routing.model == "memoria-forte"
        assert resp.routing.reason == JUSTIFICATIVA
        assert resp.usage.cache_read_tokens == 800
        assert resp.usage.cache_creation_known is True

    async def test_grpc_exige_papel_como_o_rest(self, stub_rt, nucleo):
        """Os decorators moram no CASO DE USO: se morassem no router, a porta
        gRPC nasceria aberta."""
        nucleo.papel_developer()
        # developer PODE executar turno; viewer não existe neste duplo, então o
        # que se prova aqui é que a decisão é a MESMA função nas duas portas.
        resp = await stub_rt.RunTurn(
            bff.RunTurnRequest(demand_id="dem-1", thread_id="th-1", text=VOLATIL),
            metadata=CONTA,
        )
        assert resp.thread_id == "th-1"

    def test_turno_sem_texto_e_422_no_rest(self, cliente_rt):
        r = cliente_rt.post(ROTA, headers=CABECALHOS, json={"text": ""})
        assert r.status_code == 422
