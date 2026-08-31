"""Adaptador OpenAI/Codex da porta `AgentProvider` — o SEGUNDO adaptador.

Ele existe por disciplina de ADR-0001: *uma porta com um adaptador só é
palpite*. Enquanto só houvesse o adaptador Anthropic, "porta de provedor de
agente" seria o SDK da Anthropic com outro nome — e as diferenças que a
plataforma vai pagar (cache, contagem, effort) só apareceriam no dia da troca.

**Sobre HTTP na mão em vez de SDK.** A regra deste repositório para a API da
Anthropic é usar o SDK oficial, e o adaptador de lá a cumpre. Aqui a escolha é
outra e é deliberada: `httpx` já é dependência do BFF, a superfície usada é uma
rota só (`POST /v1/chat/completions`), e trazer um segundo SDK de fornecedor
para dentro do processo — com o transitivo dele — para uma chamada custa mais
do que resolve. O contrato desta porta não muda por isso: o teste de contrato
roda igual nos dois, e é ele que garante a substituibilidade.

**As divergências, como este adaptador as trata (numeração de `ports.py`):**

D1 (cache) — Aqui o cache de prefixo é AUTOMÁTICO: não há breakpoint para
marcar, não há TTL para escolher e, sobretudo, **não há contabilidade de
CRIAÇÃO de cache**. A resposta informa só `prompt_tokens_details.cached_tokens`
(leitura). Por isso `Usage.cache_creation_tokens` é sempre 0 aqui, e o
adaptador NÃO anuncia `Capability.CACHE_CREATION_ACCOUNTING`. Isso tem
consequência de produto: o alerta de invalidador silencioso da ADR-0012 §1
("cache_read zerado em prefixo que deveria estar estável") continua funcionando,
mas a outra metade — "escreveu cache e nunca leu" — é invisível sob este
provedor. Quem lê a telemetria precisa ver a capacidade junto do número, e é
por isso que ela viaja no `Reply`.

D2 (contagem) — **Aqui está a armadilha da dupla contagem.** Neste fornecedor
`prompt_tokens` INCLUI os tokens servidos do cache; `cached_tokens` é um
SUBCONJUNTO dele. Somar os dois campos como se fossem parcelas disjuntas — que
é o que o `dop.v1.UsageEvent` do núcleo assume, porque é a semântica da
Anthropic — inflaria a medição da ADR-0011 sem erro nenhum aparecendo. Este
adaptador SUBTRAI, e a subtração é a linha mais importante do arquivo.

D3 (operador) — `role:"developer"` é o canal de autoridade deste fornecedor, e
é aceito em qualquer posição. O prefixo estável e a intervenção do operador vão
os DOIS como `developer`, o que é correto: os dois são autoria da plataforma. O
que a porta garante e este adaptador cumpre é que intervenção de operador nunca
sai como `role:"user"`.

D4 (effort) — Aqui só existem `low|medium|high`. `xhigh` e `max` são REBAIXADOS
para `high`, com aviso legível no `Reply`. Numa tarefa crítica (ADR-0007: não se
economiza no crítico) isso é decisão de produto, não detalhe de adaptador: quem
roteia para `max` e recebe `high` precisa saber que recebeu.

D5 (parada) — `stop | length | tool_calls | content_filter`, normalizados.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.runtime.credentials import Credential
from app.runtime.errors import AgentProviderUnavailable, Reason
from app.runtime.ports import (
    AgentProvider,
    Capability,
    Effort,
    ModelClass,
    ProviderInfo,
    Reply,
    Role,
    StopReason,
    Turn,
    Usage,
)

NOME = "openai"
BASE_URL = "https://api.openai.com/v1"

# Catálogo de PARTIDA deste fornecedor — a mesma natureza do `DefaultCatalog()`
# do núcleo: nomes substituíveis, não afirmação sobre o catálogo vigente da
# OpenAI. Quando o provedor virar recurso de conta (ADR-0013), o catálogo vem
# da configuração do recurso e este dicionário fica só como padrão.
CATALOGO = {
    "cheap": "gpt-5-mini",
    "medium": "gpt-5",
    "strong": "gpt-5-codex",
}

# Preço: VAZIO de propósito. Preencher esta tabela com números que eu não
# tenho seria pior que deixá-la vazia — um preço inventado alimenta o orçamento
# da ADR-0011 com ficção convincente, e ninguém confere um número plausível.
# Vazia, `price_for` devolve None e o laço do turno DIZ que não sabe calcular o
# custo deste provedor. Preencher é trabalho de quem tem a tabela do contrato,
# e o lugar certo é a configuração do recurso de categoria `agent` (ADR-0013).
PRECOS: dict = {}

# D4: os cinco níveis do núcleo → os três daqui. `xhigh` e `max` caem em
# `high`, que é o teto real — fingir que aplicou `max` seria pior que rebaixar.
_EFFORT = {
    Effort.LOW: "low",
    Effort.MEDIUM: "medium",
    Effort.HIGH: "high",
    Effort.XHIGH: "high",
    Effort.MAX: "high",
}

_PARADA = {
    "stop": StopReason.COMPLETED,
    "length": StopReason.MAX_TOKENS,
    "tool_calls": StopReason.TOOL_USE,
    "content_filter": StopReason.REFUSED,
}


class OpenAIAgentProvider(AgentProvider):
    """Adaptador OpenAI/Codex. `http_client` injetável — é o que o teste usa."""

    def __init__(
        self,
        credential: Credential | None = None,
        *,
        http_client: Any = None,
        base_url: str = BASE_URL,
        catalog: Mapping[str, str] | None = None,
    ):
        self._credential = credential
        self._http = http_client
        self._base_url = base_url.rstrip("/")
        self._catalog = dict(catalog or CATALOGO)

    @property
    def info(self) -> ProviderInfo:
        return ProviderInfo(
            name=NOME,
            catalog={ModelClass(k): v for k, v in self._catalog.items()},
            capabilities=frozenset(
                {
                    # Sem EXPLICIT_PREFIX_CACHE: o cache é automático (D1).
                    # Sem CACHE_CREATION_ACCOUNTING: criação não é reportada.
                    # Sem FULL_EFFORT_RANGE: xhigh e max não existem (D4).
                    Capability.OPERATOR_CHANNEL,
                    Capability.STRUCTURED_OUTPUT,
                }
            ),
            prices=PRECOS,
        )

    # ── montagem ────────────────────────────────────────────────────────────

    def render(
        self, turn: Turn, *, model: str, effort: Effort
    ) -> tuple[Mapping[str, object], tuple[str, ...]]:
        avisos: list[str] = []

        # O prefixo estável é a PRIMEIRA mensagem. Aqui não há breakpoint para
        # marcar (D1): a única alavanca de cache é a ordem, e é ela que este
        # adaptador respeita — estável primeiro, volátil depois.
        mensagens: list[dict[str, object]] = [
            {"role": "developer", "content": turn.stable_prefix}
        ]
        for m in turn.messages:
            if m.role is Role.OPERATOR:
                # Canal de autoridade — NUNCA `user` (D3).
                mensagens.append({"role": "developer", "content": m.text})
            elif m.role is Role.ASSISTANT:
                mensagens.append({"role": "assistant", "content": m.text})
            else:
                mensagens.append({"role": "user", "content": m.text})

        pedido: dict[str, object] = {
            "model": model,
            "messages": mensagens,
            "max_completion_tokens": turn.max_output_tokens,
        }

        aplicado = _EFFORT[effort]
        if aplicado != effort.value:
            avisos.append(
                f"effort '{effort.value}' não existe neste provedor: aplicado "
                f"'{aplicado}' (D4). Em trabalho crítico, isso é decisão de produto."
            )
        pedido["reasoning_effort"] = aplicado

        if turn.output_schema is not None:
            pedido["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "dop_turn",
                    "strict": True,
                    "schema": dict(turn.output_schema),
                },
            }
        return pedido, tuple(avisos)

    # ── envio ───────────────────────────────────────────────────────────────

    def _cliente(self):
        if self._http is not None:
            return self._http
        if self._credential is None or not self._credential:
            raise AgentProviderUnavailable(NOME, Reason.MISSING_CREDENTIAL)
        import httpx

        self._http = httpx.AsyncClient(
            base_url=self._base_url,
            headers={"authorization": f"Bearer {self._credential.reveal()}"},
            timeout=120.0,
        )
        return self._http

    async def send(self, turn: Turn, *, model: str, effort: Effort) -> Reply:
        import httpx

        cliente = self._cliente()
        pedido, avisos = self.render(turn, model=model, effort=effort)
        try:
            resposta = await cliente.post("/chat/completions", json=dict(pedido))
        except httpx.TimeoutException as exc:
            raise AgentProviderUnavailable(
                NOME, Reason.UNREACHABLE, debug_detail=f"timeout: {exc}"
            ) from exc
        except httpx.HTTPError as exc:
            raise AgentProviderUnavailable(
                NOME, Reason.UNREACHABLE, debug_detail=f"{type(exc).__name__}: {exc}"
            ) from exc

        if resposta.status_code >= 400:
            raise self._traduz_status(resposta.status_code)
        return self._para_dominio(resposta.json(), effort=effort, avisos=avisos)

    # ── tradução ────────────────────────────────────────────────────────────

    @staticmethod
    def _traduz_status(status: int) -> AgentProviderUnavailable:
        """Status HTTP → indisponibilidade do domínio (D6).

        O CORPO da resposta do fornecedor não entra em lugar nenhum: ele
        costuma ecoar cabeçalhos e, em alguns erros, um prefixo da chave.
        """
        if status in (401, 403):
            razao = Reason.REJECTED_CREDENTIAL
        elif status == 404:
            razao = Reason.UNKNOWN_MODEL
        else:
            razao = Reason.PROVIDER_ERROR
        return AgentProviderUnavailable(
            NOME, razao, debug_detail=f"HTTP {status} do provedor"
        )

    def _para_dominio(
        self, corpo: Mapping[str, Any], *, effort: Effort, avisos: tuple[str, ...]
    ) -> Reply:
        escolhas = corpo.get("choices") or [{}]
        primeira = escolhas[0] or {}
        texto = str((primeira.get("message") or {}).get("content") or "")

        uso = corpo.get("usage") or {}
        prompt = int(uso.get("prompt_tokens") or 0)
        cacheados = int((uso.get("prompt_tokens_details") or {}).get("cached_tokens") or 0)
        # ── D2, a linha que impede a dupla contagem ─────────────────────────
        # `prompt_tokens` INCLUI `cached_tokens` neste fornecedor. O núcleo
        # espera parcelas DISJUNTAS. `max(..., 0)` porque contagem negativa não
        # é resposta: se um dia o fornecedor mudar a semântica, o pior caso é
        # subestimar a entrada, não gravar um número impossível no orçamento.
        entrada = max(prompt - cacheados, 0)
        usage = Usage(
            input_tokens=entrada,
            output_tokens=int(uso.get("completion_tokens") or 0),
            cache_read_tokens=cacheados,
            # Não é zero-afirmação: é "não dá para saber" (D1). A capacidade
            # ausente em `capabilities` é o que diz isso a quem lê.
            cache_creation_tokens=0,
        )

        dados = None
        if texto.strip().startswith("{"):
            import json

            try:
                dados = json.loads(texto)
            except json.JSONDecodeError:
                dados = None

        return Reply(
            text=texto,
            usage=usage,
            model=str(corpo.get("model") or ""),
            provider=NOME,
            stop_reason=_PARADA.get(str(primeira.get("finish_reason") or ""), StopReason.UNKNOWN),
            data=dados,
            effort_applied=Effort(_EFFORT[effort]),
            capabilities=self.info.capabilities,
            warnings=avisos,
        )


__all__ = ["BASE_URL", "CATALOGO", "NOME", "PRECOS", "OpenAIAgentProvider"]
