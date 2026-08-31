"""A porta do provedor de agente — e o vocabulário de domínio que ela fala.

Provedor de agente é PORTA (ADR-0001), não dependência. A plataforma precisa
de Claude, Codex, Google Code Assist e do que vier depois, e a ADR-0013 já
decidiu como: provedor é recurso de categoria `agent`, escolhido POR CONTA e
POR PROJETO — o que significa que vários convivem ao mesmo tempo no mesmo
processo. Não é escolha de boot como o `SecretStore` do núcleo; é escolha de
requisição.

Consequência dura, e é a razão deste arquivo existir: **nada acima desta porta
pode ver tipo de fornecedor.** Nem `anthropic.Message`, nem `dict` cru de
resposta de API, nem nome de campo de SDK. O `app/runtime/loop.py` conversa só
com os tipos definidos aqui; trocar de fornecedor é trocar o adaptador.

═══════════════════════════════════════════════════════════════════════════════
O QUE ESTÁ DENTRO DA PORTA
═══════════════════════════════════════════════════════════════════════════════

Mandar uma conversa (prefixo estável + mensagens), receber resposta com uso
(entrada, saída, leitura de cache, criação de cache) e um motivo de parada.
Mais o catálogo: CLASSE de modelo → nome concreto, que é a metade do
`cost.ModelRouter` do núcleo que muda por fornecedor (ver `catalog.py`).

═══════════════════════════════════════════════════════════════════════════════
O QUE FICOU DE FORA, EXPLICITAMENTE
═══════════════════════════════════════════════════════════════════════════════

A regra das portas deste projeto: o que não é cumprível por TODOS os
adaptadores não entra — porque uma porta que só um fornecedor consegue honrar
é o fornecedor com outro nome.

  - **Ferramentas / laço de tool use.** Os formatos divergem em três eixos ao
    mesmo tempo (schema `input_schema` × `parameters`, resultado como bloco
    `tool_result` no turno do usuário × mensagem `role:"tool"` própria, e
    paralelismo por padrão × por flag). Um laço de ferramenta escrito sobre a
    média dos dois seria um laço que nenhum dos dois executa bem. `Turn` já
    carrega o campo `tools`, sempre vazio nesta entrega, para que a assinatura
    não mude quando entrar.
  - **Streaming.** O acompanhamento ao vivo da plataforma é o SSE que já
    existe (`app/usecases/stream.py`): a mensagem publicada vira evento no
    núcleo e chega sozinha ao cockpit. Um segundo caminho de streaming aqui
    seria uma segunda fonte da verdade para a mesma timeline.
  - **Janela de contexto e compaction.** Cada fornecedor tem a sua, e a
    ADR-0012 §3 já decidiu que a retomada é por RECONSTRUÇÃO (pacote + achados)
    e não por replay do transcript. Compaction é rede de segurança de sessão
    contínua — coisa do adaptador, não da porta.

═══════════════════════════════════════════════════════════════════════════════
ONDE OS FORNECEDORES DIVERGEM  (o trabalho de verdade)
═══════════════════════════════════════════════════════════════════════════════

Cada divergência abaixo tem um teste de contrato em `tests/test_runtime.py`.

**D1 — Semântica de cache de prefixo. É a mais cara, porque a ADR-0012 depende
dela.**  A Anthropic tem cache EXPLÍCITO: um `cache_control` marca o fim do
prefixo, TTL de 5 min, leitura ~0,1× e escrita 1,25×, e a resposta separa
`cache_read_input_tokens` de `cache_creation_input_tokens`. A OpenAI tem cache
AUTOMÁTICO: sem breakpoint, sem TTL controlável, e a resposta informa só o
`cached_tokens` (leitura) — **criação de cache não é reportada**. Portanto
`Usage.cache_creation_tokens` é sempre 0 no adaptador OpenAI, e isso NÃO
significa "nada foi escrito no cache": significa "não dá para saber". O alerta
de invalidador silencioso da ADR-0012 §1 (cache_read zerado em prefixo estável)
só é FIEL sob um provedor com contabilidade explícita — `Capability.
CACHE_CREATION_ACCOUNTING` diz quem tem. Quem lê a telemetria precisa saber
disso, e por isso a capacidade viaja no `Reply`.

**D2 — Contagem de tokens: a armadilha da dupla contagem.**  Na Anthropic,
`input_tokens` EXCLUI o que veio do cache — as três parcelas (entrada, leitura
de cache, criação de cache) são disjuntas e somam o total. Na OpenAI,
`prompt_tokens` INCLUI os cacheados, e `cached_tokens` é um SUBCONJUNTO dele.
Somar os campos da OpenAI como se fossem disjuntos infla a medição da ADR-0011
sem que ninguém perceba. **A porta normaliza para a semântica disjunta** (é a
que o `dop.v1.UsageEvent` do núcleo assume): o adaptador OpenAI subtrai.

**D3 — Canal do operador.**  A intervenção do operador precisa entrar no meio
da conversa sem reescrever o topo do prompt (ADR-0012 §1, spec §2). A Anthropic
tem mensagem `role:"system"` no meio de `messages` — nos modelos Opus 5/4.8 e
Fable/Mythos, e NÃO no Sonnet 5, que responde 400. A OpenAI tem `role:
"developer"`, aceito em qualquer posição. A porta expõe `Role.OPERATOR` e cada
adaptador resolve — inclusive o recuo para bloco marcado no turno do usuário
quando o modelo recusa o canal. O que a porta GARANTE é que a instrução do
operador nunca é atribuída ao usuário: é ela que autoriza, e confundir as duas
é abrir a porta para injeção.

**D4 — Effort.**  O vocabulário do núcleo é `low|medium|high|xhigh|max`
(ADR-0011 §3). A Anthropic aceita os cinco. A OpenAI aceita `low|medium|high`.
O adaptador MAPEIA e declara o que aplicou em `Reply.effort_applied` — nunca
finge que aplicou `max`. Numa tarefa crítica (ADR-0007: não se economiza no
crítico) essa diferença é decisão de produto, não detalhe, e por isso ela sai
como aviso legível em `Reply.warnings`.

**D5 — Motivo de parada.**  Anthropic: `end_turn | max_tokens | stop_sequence |
tool_use | pause_turn | refusal | model_context_window_exceeded`. OpenAI:
`stop | length | tool_calls | content_filter`. Normalizados em `StopReason` —
o domínio precisa distinguir "terminou", "foi cortado" e "recusou", e só isso.

**D6 — Indisponibilidade.**  As duas famílias de exceção não se parecem em
nada. As duas viram `AgentProviderUnavailable` (ver `errors.py`), porque
indisponibilidade de terceiro não pode chegar ao cliente como erro nosso.
"""

from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum


class ModelClass(StrEnum):
    """CLASSE de modelo, não nome — espelha `cost.ModelClass` do núcleo.

    Nome de modelo muda de líder por semestre (ADR-0001) e não sobrevive a uma
    tabela de política; a classe é o que a decisão significa. Quem resolve
    classe → nome concreto é o CATÁLOGO, e catálogo é por fornecedor: é
    exatamente a separação que `internal/domain/cost/router.go` já fez do lado
    do núcleo, e esta porta a continua do lado de cá.
    """

    CHEAP = "cheap"
    MEDIUM = "medium"
    STRONG = "strong"


class Effort(StrEnum):
    """Esforço de raciocínio, no vocabulário do núcleo (ADR-0011 §3)."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    XHIGH = "xhigh"
    MAX = "max"


class Role(StrEnum):
    """Quem fala. `OPERATOR` é canal PRÓPRIO, e a distinção é de segurança.

    Instrução do operador tem autoridade; texto do usuário não tem. Achatar as
    duas num só papel é o caminho clássico de injeção de prompt — quem escreve
    numa entrada de usuário passa a poder forjar instrução. Ver D3.
    """

    USER = "user"
    ASSISTANT = "assistant"
    OPERATOR = "operator"


class StopReason(StrEnum):
    """Por que o modelo parou, no vocabulário do DOMÍNIO (ver D5).

    Três coisas o domínio precisa distinguir e só três: terminou, foi cortado,
    recusou. `TOOL_USE` existe para que o dia em que ferramentas entrarem na
    porta não mude este enum; `UNKNOWN` para que motivo novo de fornecedor não
    vire exceção — parar de trabalhar por causa de uma string desconhecida
    seria pior que registrar que ela apareceu.
    """

    COMPLETED = "completed"
    MAX_TOKENS = "max_tokens"
    REFUSED = "refused"
    TOOL_USE = "tool_use"
    UNKNOWN = "unknown"


class Capability(StrEnum):
    """O que ESTE adaptador consegue cumprir — as divergências, como dado.

    Existe para que quem lê a telemetria saiba o que o número significa, em vez
    de descobrir na conciliação. Ver D1 e D4.
    """

    #: Prefixo cacheado marcado explicitamente (breakpoint), e não adivinhado.
    EXPLICIT_PREFIX_CACHE = "explicit_prefix_cache"
    #: A resposta separa criação de cache de leitura de cache (ADR-0012 §1).
    CACHE_CREATION_ACCOUNTING = "cache_creation_accounting"
    #: Instrução de operador tem canal próprio no protocolo do fornecedor.
    OPERATOR_CHANNEL = "operator_channel"
    #: Os cinco níveis de effort da ADR-0011 §3, sem redução.
    FULL_EFFORT_RANGE = "full_effort_range"
    #: Saída estruturada validada pelo fornecedor (achado terso, ADR-0012 §2).
    STRUCTURED_OUTPUT = "structured_output"


@dataclass(frozen=True, slots=True)
class Message:
    """Uma fala. A parte VOLÁTIL da conversa — vai depois do breakpoint."""

    role: Role
    text: str


@dataclass(frozen=True, slots=True)
class Usage:
    """Consumo de uma chamada, com as quatro parcelas DISJUNTAS (ver D2).

    Disjuntas é o contrato: `input_tokens` não inclui o que veio do cache. O
    `dop.v1.UsageEvent` do núcleo assume isso, e um adaptador que devolvesse a
    contagem inclusiva inflaria o orçamento da ADR-0011 em silêncio.
    """

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0


@dataclass(frozen=True, slots=True)
class Price:
    """Preço de um modelo, em MICROS por 1.000 tokens, com a moeda junto.

    Por 1.000 e não por token: a leitura de cache custa 0,1× do input, e um
    preço por token viraria fração — que em `int` some e em `float` mente. A
    mesma disciplina do `usecases/cost.py`: dinheiro é inteiro em micros, e não
    há um `/ 1_000_000` em lugar nenhum deste caminho.

    Por que o preço mora no ADAPTADOR: é ele que sabe qual fornecedor cobrou o
    quê. `NewUsage.cost_micros` do núcleo é preenchido por quem chamou o modelo
    justamente por isso — a borda não recalcula preço de terceiro, ela informa
    o que gastou.
    """

    currency: str
    input_per_1k: int
    output_per_1k: int
    cache_read_per_1k: int
    cache_creation_per_1k: int

    def cost_micros(self, usage: Usage) -> int:
        """Custo em micros, aritmética de INTEIRO do começo ao fim."""
        total = (
            usage.input_tokens * self.input_per_1k
            + usage.output_tokens * self.output_per_1k
            + usage.cache_read_tokens * self.cache_read_per_1k
            + usage.cache_creation_tokens * self.cache_creation_per_1k
        )
        return total // 1000


@dataclass(frozen=True, slots=True)
class Turn:
    """A conversa a enviar: prefixo estável primeiro, volátil depois.

    A separação é a ADR-0012 §1 tornada TIPO. Enquanto `stable_prefix` for um
    campo próprio, ninguém consegue interpolar o texto do turno dentro dele por
    distração — o pior jeito de perder a economia de cache, porque não falha,
    só fica caro. `fingerprint()` é o que o teste de contrato compara entre
    turnos.
    """

    stable_prefix: str
    messages: tuple[Message, ...]
    #: Schema JSON da resposta. Achado terso e validado (ADR-0012 §2).
    output_schema: Mapping[str, object] | None = None
    #: Sempre vazio nesta entrega — ver "O QUE FICOU DE FORA".
    tools: tuple[Mapping[str, object], ...] = ()
    max_output_tokens: int = 8192

    def fingerprint(self) -> str:
        """Impressão digital do PREFIXO, e só dele.

        Dois turnos da mesma thread têm de dar a mesma impressão, mesmo com
        mensagens diferentes: é isso que significa "o prefixo está estável".
        """
        return hashlib.sha256(self.stable_prefix.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class Reply:
    """A resposta, em tipos do DOMÍNIO. Nenhum objeto de SDK atravessa daqui."""

    text: str
    usage: Usage
    model: str
    provider: str
    stop_reason: StopReason = StopReason.COMPLETED
    #: Saída estruturada já validada, quando houve `output_schema`.
    data: Mapping[str, object] | None = None
    #: O effort REALMENTE aplicado. Pode ser menor que o pedido — ver D4.
    effort_applied: Effort = Effort.HIGH
    #: O que este provedor cumpre. Viaja junto para a telemetria ser legível.
    capabilities: frozenset[Capability] = frozenset()
    #: Avisos legíveis (effort rebaixado, canal de operador recuado, …).
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ProviderInfo:
    """Ficha do adaptador — o que a borda mostra e o relatório audita."""

    name: str
    catalog: Mapping[ModelClass, str]
    capabilities: frozenset[Capability] = field(default_factory=frozenset)
    #: Preço por nome CONCRETO de modelo. Modelo ausente = preço desconhecido,
    #: e desconhecido NÃO vira zero em silêncio — ver `price_for`.
    prices: Mapping[str, Price] = field(default_factory=dict)


class AgentProvider(ABC):
    """A porta. Um adaptador por fornecedor, todos com este contrato.

    Garantias que a suíte de contrato verifica em TODOS os adaptadores:

      1. `resolve_model` devolve nome concreto e não-vazio para as três
         classes, e é PURA (mesma classe, mesmo nome, sem I/O);
      2. `render` põe o prefixo estável ANTES das mensagens na requisição
         serializada — é a economia da ADR-0012, e ela quebra sem barulho;
      3. `render` é determinístico: mesmo `Turn`, mesmos bytes;
      4. a mensagem de operador nunca é serializada como fala do usuário (D3);
      5. `send` devolve `Usage` com as quatro parcelas disjuntas (D2);
      6. `stop_reason` está sempre no vocabulário do domínio (D5);
      7. credencial ausente e rede indisponível viram
         `AgentProviderUnavailable`, nunca exceção do SDK (D6);
      8. a credencial não aparece em `repr()`, `str()` nem em `render()`.
    """

    @property
    @abstractmethod
    def info(self) -> ProviderInfo:
        """Nome, catálogo e capacidades. Sem I/O — é ficha, não consulta."""

    @property
    def name(self) -> str:
        return self.info.name

    def resolve_model(self, model_class: ModelClass) -> str:
        """CLASSE → nome concreto de modelo, pelo catálogo DESTE fornecedor.

        Classe fora do catálogo não para trabalho: cai na classe forte e o
        adaptador avisa. É a mesma escolha do `Router.Route` do núcleo — na
        dúvida não se economiza, porque o gasto aparece na medição e o
        retrabalho não.
        """
        catalogo = self.info.catalog
        return catalogo.get(model_class) or catalogo[ModelClass.STRONG]

    def supports(self, capability: Capability) -> bool:
        return capability in self.info.capabilities

    def price_for(self, model: str) -> Price | None:
        """Preço deste modelo, ou `None` quando não se sabe.

        `None` e não um preço zerado: zero afirmaria que a chamada foi de
        graça, e um orçamento alimentado com zeros é exatamente a ficção que a
        ADR-0011 §2 existe para impedir. Quem recebe `None` precisa DIZER que
        não sabe — é o que o laço do turno faz, com aviso legível.
        """
        return self.info.prices.get(model)

    @abstractmethod
    def render(
        self, turn: Turn, *, model: str, effort: Effort
    ) -> tuple[Mapping[str, object], tuple[str, ...]]:
        """Monta a requisição do fornecedor SEM enviá-la.

        Existe por dois motivos que valem o método público: é o que permite à
        suíte de contrato auditar a ordem do prefixo em QUALQUER fornecedor
        (procurando as duas fatias na requisição serializada, sem conhecer o
        formato de nenhum), e é o que permite registrar o que foi enviado sem
        gastar uma chamada. Devolve (requisição, avisos).
        """

    @abstractmethod
    async def send(self, turn: Turn, *, model: str, effort: Effort) -> Reply:
        """Envia a conversa. Falha SEMPRE como `AgentProviderUnavailable`."""


def serialize_for_audit(request: Mapping[str, object]) -> str:
    """A requisição como texto, para a suíte de contrato inspecionar a ORDEM.

    `sort_keys=False` de propósito: o que se audita aqui é a ordem em que o
    adaptador montou as coisas, e ordenar as chaves apagaria justamente o que
    está sendo verificado.
    """
    import json

    return json.dumps(request, ensure_ascii=False, default=str)


__all__ = [
    "AgentProvider",
    "Capability",
    "Effort",
    "Message",
    "ModelClass",
    "Price",
    "ProviderInfo",
    "Reply",
    "Role",
    "StopReason",
    "Turn",
    "Usage",
    "serialize_for_audit",
]
