"""Política × catálogo: a separação que o núcleo já fez, continuada aqui.

`internal/domain/cost/router.go` do dop-core separa duas coisas que mudam por
motivos e em ritmos diferentes:

  - a **política** (que CLASSE de modelo para que tipo de trabalho) muda com
    telemetria — é a pendência P-7 da ADR-0011;
  - o **catálogo** (que modelo concreto é a classe forte hoje) muda quando um
    fornecedor lança modelo novo.

Amarrar os dois obrigaria a revisar a política a cada lançamento. E há um
terceiro motivo, que é o desta entrega: **catálogo é por FORNECEDOR**. A classe
forte da Anthropic e a classe forte da OpenAI são modelos diferentes, com
nomes, preços e limites diferentes — e a política não deveria nem saber disso.

O problema de fronteira que este módulo resolve: `dop.v1.RoutingDecision` NÃO
carrega a classe. O núcleo decide a classe internamente e devolve só o nome
concreto do SEU catálogo (`cost.DefaultCatalog()`: claude-haiku / claude-sonnet
/ claude-opus). O BFF, que precisa da CLASSE para pedir ao adaptador ativo o
nome dele, tem de desfazer o caminho — e é isso que a tabela abaixo faz.

Isso é remendo de contrato, e está registrado como tal: a saída certa é o
núcleo publicar `class` em `RoutingDecision`, e aí este módulo vira duas
linhas. Enquanto não vier, a regra é conservadora: **nome que a tabela não
conhece é tratado como nome CONCRETO e passa intacto**. Adivinhar a classe de
um nome desconhecido trocaria silenciosamente o modelo que o núcleo escolheu —
e o log de custo mostraria a troca só depois da fatura.
"""

from __future__ import annotations

from app.runtime.ports import Effort, ModelClass

# Espelha `cost.DefaultCatalog()` do núcleo. Se o catálogo do núcleo mudar,
# esta tabela precisa mudar junto — e é justamente por essa fragilidade que a
# solução de verdade é o núcleo publicar a classe (ver o docstring).
_CLASSE_POR_MODELO_DO_NUCLEO: dict[str, ModelClass] = {
    "claude-haiku": ModelClass.CHEAP,
    "claude-sonnet": ModelClass.MEDIUM,
    "claude-opus": ModelClass.STRONG,
    # O núcleo devolve a própria classe como nome quando o catálogo dele está
    # incompleto (`Router.Route`). Reconhecer isso evita tratar "strong" como
    # nome de modelo e mandá-lo para a API.
    "cheap": ModelClass.CHEAP,
    "medium": ModelClass.MEDIUM,
    "strong": ModelClass.STRONG,
}


def class_of(model_from_core: str) -> ModelClass | None:
    """Nome vindo do `RouteModel` do núcleo → CLASSE, quando dá para saber.

    `None` significa "isto já é um nome concreto de modelo" — e nesse caso o
    laço do turno usa o nome como veio. `None` NÃO é "não sei, chuta forte":
    chutar aqui desfaria a decisão do núcleo sem avisar ninguém.
    """
    return _CLASSE_POR_MODELO_DO_NUCLEO.get(model_from_core.strip().lower())


def effort_of(effort_from_core: str, *, default: Effort = Effort.HIGH) -> Effort:
    """Effort do núcleo → vocabulário da porta, com queda segura.

    Valor desconhecido cai no padrão ALTO e não em `low`: a ADR-0011 §3 já
    decidiu que, na dúvida, não se economiza — errar para caro aparece na
    medição, errar para barato aparece em retrabalho, que não aparece.
    """
    try:
        return Effort(effort_from_core.strip().lower())
    except ValueError:
        return default


__all__ = ["class_of", "effort_of"]
