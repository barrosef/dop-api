"""⚠️ PROVISÓRIO — de onde sai a credencial do provedor de agente.

═══════════════════════════════════════════════════════════════════════════════
ISTO É UM STUB DELIBERADO. NÃO É A SOLUÇÃO. NÃO COPIE ESTE PADRÃO.
═══════════════════════════════════════════════════════════════════════════════

O desenho da plataforma diz outra coisa: provedor de agente é **recurso de
categoria `agent`** (ADR-0013), com a credencial no cofre atrás de
`ports.SecretStore`, no NÚCLEO. E o núcleo, por desenho e com teste guardando,
**nunca devolve segredo**: `GetResource` devolve um rótulo opaco
(`credential_ref`), nunca o valor.

Daí o problema de fronteira desta entrega, que é decisão de ARQUITETURA e não
de implementação: o runtime vive no BFF (ADR-0016) e precisa da chave para
falar com o modelo; o cofre vive no núcleo, que não a entrega. As saídas
possíveis estão descritas no relatório desta entrega, com recomendação.

O que **não** se fez aqui, e é a razão de o stub existir: **não se criou uma
RPC que devolve segredo.** Um `GetCredential` no contrato do núcleo resolveria
a entrega de hoje e desfaria o isolamento de credenciais que a plataforma
inteira construiu — a partir dele, todo cliente do núcleo passa a poder pedir
o segredo, e o teste que guarda o rótulo opaco vira decoração.

Enquanto a decisão não vem, a chave sai de variável de ambiente do PROCESSO —
uma chave por instalação, não por conta. Consequências que precisam estar
escritas em algum lugar, e este é o lugar:

  - **não há isolamento por conta.** Todas as contas do BFF gastam na mesma
    chave. Em multi-tenant isso é inaceitável em produção — é a razão pela qual
    isto é provisório e não "simples";
  - **não há atribuição de custo por credencial.** A medição da ADR-0011
    continua por demanda/conta (é o núcleo que a grava), mas a FATURA do
    fornecedor é uma só;
  - **não há revogação por recurso.** Revogar a concessão `use` de um recurso
    de categoria `agent` não tira o acesso de ninguém, porque o acesso não
    passa pelo recurso.

`Credential` redige o valor em `repr` e `str` pelo mesmo motivo que o
`SecretValue` do núcleo: segredo que sabe se imprimir vaza em log de exceção,
em `%r` de dataclass e em traceback — os três lugares onde ninguém está olhando
quando acontece.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from app.runtime.errors import AgentProviderUnavailable, Reason

#: Provedor usado quando a requisição não escolhe. Lido do ambiente porque
#: `app/settings.py` é do dono do repositório — ver o relatório: o registro
#: definitivo é `settings.agent_provider`.
ENV_PROVEDOR_PADRAO = "DOP_AGENT_PROVIDER"

#: Variável de ambiente por provedor. PROVISÓRIO — ver o cabeçalho.
ENV_POR_PROVEDOR: dict[str, str] = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
}


@dataclass(frozen=True, slots=True)
class Credential:
    """Um segredo que se recusa a ser impresso.

    O valor só sai por `reveal()` — um nome que aparece no diff e que ninguém
    escreve por distração, ao contrário de `str(cred)`.
    """

    _value: str

    def reveal(self) -> str:
        return self._value

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return "Credential(***)"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return "***"

    def __bool__(self) -> bool:
        return bool(self._value)


def default_provider() -> str:
    return os.getenv(ENV_PROVEDOR_PADRAO, "anthropic").strip().lower() or "anthropic"


def credential_for(provider: str) -> Credential:
    """A credencial do provedor. PROVISÓRIO: variável de ambiente do processo.

    Ausência é `AgentProviderUnavailable(MISSING_CREDENTIAL)` e não `None`:
    seguir adiante sem chave produziria um 401 do fornecedor lá na frente,
    depois de já ter montado contexto e gasto duas idas ao núcleo — e o erro
    que chegaria ao usuário falaria de autenticação, não de configuração.
    """
    env = ENV_POR_PROVEDOR.get(provider)
    valor = os.getenv(env, "").strip() if env else ""
    if not valor:
        raise AgentProviderUnavailable(
            provider,
            Reason.MISSING_CREDENTIAL,
            debug_detail=f"variável {env or '(provedor sem variável mapeada)'} vazia",
        )
    return Credential(valor)


__all__ = [
    "ENV_POR_PROVEDOR",
    "ENV_PROVEDOR_PADRAO",
    "Credential",
    "credential_for",
    "default_provider",
]
