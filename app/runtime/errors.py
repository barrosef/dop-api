"""Falha de TERCEIRO não pode virar erro nosso.

A regra: se o provedor de agente está inacessível — sem credencial, sem rede,
modelo inexistente, cota estourada do lado dele — o cliente precisa conseguir
distinguir isso de um bug do BFF. Um 500 genérico manda o time errado
investigar, e manda o usuário esperar por um conserto que não existe.

Por isso `AgentProviderUnavailable` carrega três coisas legíveis por máquina:
QUEM (o provedor), POR QUÊ (a razão, vocabulário fechado) e o que o humano
deve fazer. E `__str__` NUNCA inclui a mensagem crua do SDK do fornecedor:
mensagem de erro de API carrega URL, cabeçalho e às vezes um prefixo da chave.
O detalhe cru fica em `debug_detail`, que vai para o log (onde o mascaramento
do `app/platform/logging/config.py` ainda passa por cima) e não para a
resposta.
"""

from __future__ import annotations

from enum import StrEnum


class Reason(StrEnum):
    """Por que o provedor não atendeu. Fechado de propósito: o cliente decide
    o que fazer a partir DAQUI, e um vocabulário aberto viraria `str.contains`
    espalhado por três clientes."""

    #: Não há credencial configurada para este provedor nesta instalação.
    MISSING_CREDENTIAL = "missing_credential"
    #: A credencial existe e foi recusada (401/403 do fornecedor).
    REJECTED_CREDENTIAL = "rejected_credential"
    #: Rede: DNS, timeout, conexão recusada, egress bloqueado.
    UNREACHABLE = "unreachable"
    #: O fornecedor respondeu erro (5xx, sobrecarga, limite de taxa).
    PROVIDER_ERROR = "provider_error"
    #: O modelo pedido não existe no catálogo do fornecedor.
    UNKNOWN_MODEL = "unknown_model"
    #: O provedor pedido não tem adaptador nesta instalação.
    UNKNOWN_PROVIDER = "unknown_provider"


#: O que o humano faz com cada razão. Escrito UMA vez, aqui: três clientes
#: redigindo a mesma orientação é como dois deles a escrevem errado.
_ORIENTACAO: dict[Reason, str] = {
    Reason.MISSING_CREDENTIAL: (
        "configure a credencial do provedor de agente (recurso de categoria "
        "'agent', ADR-0013) — nesta entrega ela é lida de variável de ambiente"
    ),
    Reason.REJECTED_CREDENTIAL: "a credencial do provedor foi recusada; renove-a",
    Reason.UNREACHABLE: (
        "o provedor de agente não respondeu; verifique rede e allowlist de egress"
    ),
    Reason.PROVIDER_ERROR: "o provedor de agente falhou; tente de novo mais tarde",
    Reason.UNKNOWN_MODEL: (
        "o modelo roteado não existe no catálogo deste provedor; ajuste o "
        "catálogo do adaptador ou o ModelRouter (ADR-0011 §3)"
    ),
    Reason.UNKNOWN_PROVIDER: "não há adaptador para este provedor de agente nesta instalação",
}


class AgentProviderUnavailable(Exception):
    """Indisponibilidade do provedor de agente — distinguível de erro nosso.

    Não herda de `HTTPException` nem de `AioRpcError` de propósito: o domínio
    não conhece transporte. Quem traduz é o adaptador de borda
    (`app/routers/runtime.py` → 502; `app/grpcapi/runtime.py` → UNAVAILABLE).
    """

    def __init__(
        self,
        provider: str,
        reason: Reason,
        *,
        debug_detail: str = "",
    ) -> None:
        self.provider = provider
        self.reason = reason
        # NÃO entra em __str__ nem na resposta: mensagem de erro de API do
        # fornecedor carrega URL, cabeçalho e, em alguns casos, prefixo de
        # chave. Vai só para o log, onde a máscara automática ainda passa.
        self.debug_detail = debug_detail
        super().__init__(self.message)

    @property
    def guidance(self) -> str:
        return _ORIENTACAO.get(self.reason, "")

    @property
    def message(self) -> str:
        return (
            f"provedor de agente '{self.provider}' indisponível "
            f"({self.reason.value}): {self.guidance}"
        )

    def __str__(self) -> str:
        return self.message


__all__ = ["AgentProviderUnavailable", "Reason"]
