"""O AgentRuntime — a peça que faz a plataforma EXECUTAR trabalho.

Vive no BFF, e não no núcleo, por decisão registrada (ADR-0016): *o core decide
O QUÊ — fluxo, ficha, orçamento, roteamento —; o BFF executa a conversa com o
modelo*. O que este pacote contém:

    ports.py        a porta `AgentProvider` e o vocabulário de domínio, com as
                    divergências entre fornecedores documentadas (D1…D6)
    catalog.py      classe de modelo × nome concreto — a separação que o
                    `cost.ModelRouter` do núcleo já fez, continuada aqui
    prompt.py       montagem determinística: prefixo estável → breakpoint →
                    conversa (ADR-0012 §1)
    credentials.py  ⚠️ PROVISÓRIO — de onde sai a chave do fornecedor
    adapters/       dois adaptadores reais: Anthropic e OpenAI/Codex
    loop.py         o laço do turno: contexto → roteamento → modelo → medição
                    → mensagens → achado, e a pausa por orçamento
"""

from app.runtime.errors import AgentProviderUnavailable, Reason
from app.runtime.ports import (
    AgentProvider,
    Capability,
    Effort,
    Message,
    ModelClass,
    Reply,
    Role,
    StopReason,
    Turn,
    Usage,
)

__all__ = [
    "AgentProvider",
    "AgentProviderUnavailable",
    "Capability",
    "Effort",
    "Message",
    "ModelClass",
    "Reason",
    "Reply",
    "Role",
    "StopReason",
    "Turn",
    "Usage",
]
