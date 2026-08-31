"""Adaptador Anthropic da porta `AgentProvider`.

Escrito contra o SDK oficial (`anthropic` 1.x), que é a única forma suportada
de falar com essa API — nada de `httpx` na mão aqui, ao contrário do adaptador
OpenAI, onde a escolha é outra e está justificada lá.

O que este adaptador cumpre e o outro não (ver as divergências em `ports.py`):

  - **cache de prefixo EXPLÍCITO** (D1): o prefixo estável vai como bloco de
    `system` com `cache_control: ephemeral`. O breakpoint é o fim do prefixo, e
    não o fim do prompt — pôr o marcador depois da mensagem do turno escreveria
    uma entrada de cache nova a cada turno e não leria nenhuma;
  - **contabilidade de criação de cache** (D1): `cache_creation_input_tokens` e
    `cache_read_input_tokens` vêm separados, que é o que a telemetria da
    ADR-0012 §1 precisa para acusar invalidador silencioso;
  - **os cinco níveis de effort** (D4), incluindo o `max` que a ADR-0007 exige
    no crítico.

O que ele faz de diferente do óbvio:

  - **`thinking: adaptive`.** O orçamento fixo de tokens de raciocínio
    (`budget_tokens`) foi REMOVIDO nos modelos atuais e devolve 400. Quem
    controla profundidade é `output_config.effort`, que é justamente o segundo
    eixo da ADR-0011 §3 — as duas decisões encaixam sem tradução;
  - **mensagem de operador com recuo.** `role:"system"` no meio de `messages` é
    o canal não-forjável e preserva o prefixo, mas não existe em todo modelo
    (o Sonnet 5 responde 400). Em vez de manter uma lista de modelos que
    envelhece em silêncio, o adaptador TENTA e, no 400 específico, refaz a
    chamada com a instrução marcada dentro do turno do usuário — e avisa.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from app.platform.logging.config import get_logger
from app.runtime.credentials import Credential
from app.runtime.errors import AgentProviderUnavailable, Reason
from app.runtime.ports import (
    AgentProvider,
    Capability,
    Effort,
    ModelClass,
    Price,
    ProviderInfo,
    Reply,
    Role,
    StopReason,
    Turn,
    Usage,
)

NOME = "anthropic"

# Catálogo DESTE fornecedor: classe → nome concreto. Muda quando a Anthropic
# lança modelo; a política da ADR-0011 §3 não muda junto (ver `catalog.py`).
CATALOGO = {
    # As classes espelham `cost.ModelClass` do núcleo.
    "cheap": "claude-haiku-4-5",
    "medium": "claude-sonnet-5",
    "strong": "claude-opus-5",
}

# Tabela de preço, em MICROS por 1.000 tokens (USD). Leitura de cache a 0,1× do
# input e escrita a 1,25× — é essa razão que faz a economia da ADR-0012 valer a
# disciplina do prefixo estável, e é ela que precisa aparecer na medição.
#
# É tabela de PARTIDA e envelhece: preço muda, e quando mudar é aqui que se
# mexe. Modelo fora dela devolve `None` em `price_for`, e o laço do turno diz
# que não sabe — nunca grava zero (ver `ports.Price`).
PRECOS = {
    "claude-opus-5": Price("USD", 5_000, 25_000, 500, 6_250),
    "claude-sonnet-5": Price("USD", 3_000, 15_000, 300, 3_750),
    "claude-haiku-4-5": Price("USD", 1_000, 5_000, 100, 1_250),
}

# Motivo de parada do fornecedor → vocabulário do domínio (D5).
_PARADA = {
    "end_turn": StopReason.COMPLETED,
    "stop_sequence": StopReason.COMPLETED,
    "max_tokens": StopReason.MAX_TOKENS,
    "model_context_window_exceeded": StopReason.MAX_TOKENS,
    "refusal": StopReason.REFUSED,
    "tool_use": StopReason.TOOL_USE,
    "pause_turn": StopReason.TOOL_USE,
}


class AnthropicAgentProvider(AgentProvider):
    """Adaptador Anthropic. O cliente pode ser injetado — é o que o teste usa.

    `client` injetado não é concessão ao teste: é a mesma porta de entrada que
    permite apontar o adaptador para um gateway corporativo ou para a Bedrock
    sem tocar no laço do turno.
    """

    def __init__(self, credential: Credential | None = None, *, client: Any = None):
        self._credential = credential
        self._client = client

    @property
    def info(self) -> ProviderInfo:
        return ProviderInfo(
            name=NOME,
            catalog={ModelClass(k): v for k, v in CATALOGO.items()},
            capabilities=frozenset(
                {
                    Capability.EXPLICIT_PREFIX_CACHE,
                    Capability.CACHE_CREATION_ACCOUNTING,
                    Capability.OPERATOR_CHANNEL,
                    Capability.FULL_EFFORT_RANGE,
                    Capability.STRUCTURED_OUTPUT,
                }
            ),
            prices=PRECOS,
        )

    # ── montagem ────────────────────────────────────────────────────────────

    def render(
        self, turn: Turn, *, model: str, effort: Effort, operator_inline: bool = False
    ) -> tuple[Mapping[str, object], tuple[str, ...]]:
        """A requisição, sem enviá-la. A CREDENCIAL NÃO ENTRA AQUI.

        Ela vai no cliente HTTP, e não no corpo — o que faz de `render` uma
        superfície segura para log e para a suíte de contrato inspecionar.
        """
        avisos: list[str] = []

        conteudo_usuario: list[dict[str, object]] = []
        mensagens: list[dict[str, object]] = []
        for m in turn.messages:
            if m.role is Role.OPERATOR and not operator_inline:
                # Canal próprio: `role:"system"` DEPOIS da história preserva o
                # prefixo cacheado (D3). Nunca `role:"user"`.
                mensagens.append({"role": "system", "content": m.text})
            elif m.role is Role.OPERATOR:
                conteudo_usuario.append(
                    {
                        "type": "text",
                        "text": (
                            "<intervencao-do-operador>\n"
                            f"{m.text}\n</intervencao-do-operador>"
                        ),
                    }
                )
                avisos.append(
                    "modelo sem canal de operador nativo: a instrução foi marcada "
                    "dentro do turno do usuário (D3)"
                )
            elif m.role is Role.ASSISTANT:
                mensagens.append({"role": "assistant", "content": m.text})
            else:
                conteudo_usuario.append({"type": "text", "text": m.text})

        if conteudo_usuario:
            # O turno do usuário entra ANTES de qualquer mensagem de operador
            # já enfileirada: a mensagem `system` do meio precisa seguir um
            # turno de usuário, e é a última entrada de `messages`.
            mensagens.insert(0, {"role": "user", "content": conteudo_usuario})

        pedido: dict[str, object] = {
            "model": model,
            "max_tokens": turn.max_output_tokens,
            # O PREFIXO, com o breakpoint no fim dele — e não no fim do prompt.
            "system": [
                {
                    "type": "text",
                    "text": turn.stable_prefix,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            "messages": mensagens,
            "thinking": {"type": "adaptive"},
        }

        output_config: dict[str, object] = {"effort": effort.value}
        if turn.output_schema is not None:
            output_config["format"] = {
                "type": "json_schema",
                "schema": dict(turn.output_schema),
            }
        pedido["output_config"] = output_config
        return pedido, tuple(avisos)

    # ── envio ───────────────────────────────────────────────────────────────

    def _cliente(self):
        if self._client is not None:
            return self._client
        if self._credential is None or not self._credential:
            raise AgentProviderUnavailable(NOME, Reason.MISSING_CREDENTIAL)
        import anthropic

        self._client = anthropic.AsyncAnthropic(api_key=self._credential.reveal())
        return self._client

    async def send(self, turn: Turn, *, model: str, effort: Effort) -> Reply:
        import anthropic

        cliente = self._cliente()
        pedido, avisos = self.render(turn, model=model, effort=effort)
        try:
            resposta = await cliente.messages.create(**pedido)
        except anthropic.BadRequestError as exc:
            if not self._e_canal_de_operador(exc):
                raise self._traduz(exc) from exc
            # Recuo documentado (D3): o modelo não aceita `role:"system"` no
            # meio. Refaz com a instrução marcada no turno do usuário.
            get_logger().info(
                "canal de operador indisponível no modelo; recuando para bloco marcado",
                provider=NOME,
                model=model,
            )
            pedido, avisos = self.render(
                turn, model=model, effort=effort, operator_inline=True
            )
            try:
                resposta = await cliente.messages.create(**pedido)
            except Exception as exc2:  # noqa: BLE001 - tradução na fronteira
                raise self._traduz(exc2) from exc2
        except Exception as exc:  # noqa: BLE001 - tradução na fronteira
            raise self._traduz(exc) from exc

        return self._para_dominio(resposta, effort=effort, avisos=avisos)

    # ── tradução ────────────────────────────────────────────────────────────

    @staticmethod
    def _e_canal_de_operador(exc: Exception) -> bool:
        texto = str(getattr(exc, "message", "") or exc).lower()
        return "role" in texto and "system" in texto

    @staticmethod
    def _traduz(exc: Exception) -> AgentProviderUnavailable:
        """Exceção do SDK → indisponibilidade do domínio (D6).

        Nada da mensagem crua entra na resposta: ela carrega URL, cabeçalho e
        às vezes prefixo de chave. Vai para `debug_detail`, que é log.
        """
        import anthropic

        if isinstance(exc, AgentProviderUnavailable):
            return exc
        detalhe = f"{type(exc).__name__}: {exc}"
        if isinstance(exc, anthropic.AuthenticationError | anthropic.PermissionDeniedError):
            razao = Reason.REJECTED_CREDENTIAL
        elif isinstance(exc, anthropic.NotFoundError):
            razao = Reason.UNKNOWN_MODEL
        elif isinstance(exc, anthropic.APIConnectionError | anthropic.APITimeoutError):
            razao = Reason.UNREACHABLE
        else:
            razao = Reason.PROVIDER_ERROR
        return AgentProviderUnavailable(NOME, razao, debug_detail=detalhe)

    def _para_dominio(self, resposta: Any, *, effort: Effort, avisos: tuple[str, ...]) -> Reply:
        texto = "".join(
            bloco.text
            for bloco in getattr(resposta, "content", [])
            if getattr(bloco, "type", "") == "text"
        )
        uso = getattr(resposta, "usage", None)
        # As três parcelas são DISJUNTAS neste fornecedor (D2): `input_tokens`
        # já exclui o que veio do cache. Nada a subtrair aqui — e é justamente
        # o adaptador OpenAI que precisa subtrair.
        usage = Usage(
            input_tokens=int(getattr(uso, "input_tokens", 0) or 0),
            output_tokens=int(getattr(uso, "output_tokens", 0) or 0),
            cache_read_tokens=int(getattr(uso, "cache_read_input_tokens", 0) or 0),
            cache_creation_tokens=int(getattr(uso, "cache_creation_input_tokens", 0) or 0),
        )
        dados = None
        if texto.strip().startswith("{"):
            try:
                dados = json.loads(texto)
            except json.JSONDecodeError:
                dados = None
        return Reply(
            text=texto,
            usage=usage,
            model=str(getattr(resposta, "model", "") or ""),
            provider=NOME,
            stop_reason=_PARADA.get(
                str(getattr(resposta, "stop_reason", "") or ""), StopReason.UNKNOWN
            ),
            data=dados,
            effort_applied=effort,
            capabilities=self.info.capabilities,
            warnings=avisos,
        )


__all__ = ["CATALOGO", "NOME", "PRECOS", "AnthropicAgentProvider"]
