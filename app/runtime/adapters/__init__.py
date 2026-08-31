"""Registro dos adaptadores de provedor de agente.

Um dicionário e uma função — de propósito. A escolha do provedor é POR
REQUISIÇÃO (ADR-0013: provedor de agente é recurso de conta, e várias contas
convivem no mesmo processo), então não há "o adaptador" montado no boot como
acontece com o `SecretStore` do núcleo. O que existe aqui é a fábrica.

Provedor desconhecido é `AgentProviderUnavailable(UNKNOWN_PROVIDER)`, e não
uma queda silenciosa para o padrão: cair para outro fornecedor sem avisar
trocaria o modelo, o preço e a semântica de cache de uma demanda inteira, e o
único lugar onde isso apareceria seria a fatura.
"""

from __future__ import annotations

from collections.abc import Callable

from app.runtime.adapters.anthropic_provider import AnthropicAgentProvider
from app.runtime.adapters.openai_provider import OpenAIAgentProvider
from app.runtime.credentials import credential_for, default_provider
from app.runtime.errors import AgentProviderUnavailable, Reason
from app.runtime.ports import AgentProvider

FABRICAS: dict[str, Callable[[], AgentProvider]] = {
    "anthropic": lambda: AnthropicAgentProvider(credential_for("anthropic")),
    "openai": lambda: OpenAIAgentProvider(credential_for("openai")),
}


def provider_names() -> tuple[str, ...]:
    return tuple(sorted(FABRICAS))


def provider_for(name: str = "") -> AgentProvider:
    """O adaptador do provedor pedido, ou o padrão da instalação.

    A credencial é resolvida AQUI, na criação — antes de montar contexto e de
    gastar as idas ao núcleo. Descobrir que não há chave depois de duas RPCs é
    desperdício, e o erro que chegaria ao usuário falaria da chamada errada.
    """
    escolhido = (name or default_provider()).strip().lower()
    fabrica = FABRICAS.get(escolhido)
    if fabrica is None:
        raise AgentProviderUnavailable(escolhido, Reason.UNKNOWN_PROVIDER)
    return fabrica()


__all__ = [
    "FABRICAS",
    "AnthropicAgentProvider",
    "OpenAIAgentProvider",
    "provider_for",
    "provider_names",
]
