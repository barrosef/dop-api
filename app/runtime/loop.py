"""O laço do turno — conversar com o modelo e INTERPRETAR o que voltou.

Divisão de trabalho com `app/usecases/runtime.py`, e ela é deliberada:

  - **aqui** fica tudo o que não toca o núcleo: montar o turno a partir do
    pacote de contexto, chamar a porta do provedor, e transformar a resposta
    estruturada em fatos do domínio (o que responder, se concluiu, qual
    achado). Nada neste arquivo abre gRPC;
  - **lá** fica o ciclo com o núcleo — contexto, roteamento, medição,
    mensagens, achado, pausa por orçamento —, porque neste repositório a regra
    de negócio mora no caso de uso e os transversais (`@log`, `@account_scoped`)
    decoram o caso de uso, não o adaptador (ver `app/usecases/identity.py`).

Separar assim tem uma consequência prática que vale o arquivo: **a conversa com
o modelo é testável sem núcleo nenhum**, e o ciclo com o núcleo é testável sem
fornecedor nenhum. As duas metades do runtime falham por motivos diferentes, e
misturá-las num arquivo só faria toda falha parecer a mesma.

**Sobre "concluir exige publicar achado".** A spec de conversação §1 diz que a
thread não morre em silêncio. Aqui isso vira uma checagem: se o modelo marcou
`concluded` mas não escreveu título nem resumo, a conclusão é RECUSADA — a
thread continua ativa e a resposta ganha um aviso. Aceitar a conclusão vazia
seria deixar a thread morrer em silêncio com um `true` de enfeite.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.runtime.ports import (
    AgentProvider,
    Capability,
    Effort,
    Reply,
    StopReason,
    Turn,
)


@dataclass(frozen=True, slots=True)
class Finding:
    """O achado, já na forma que o `PublishFinding` do núcleo espera."""

    title: str
    payload: dict


@dataclass(frozen=True, slots=True)
class TurnExecution:
    """O que o turno produziu, em fatos do domínio.

    `reply` é sempre texto para a thread — mesmo quando o modelo devolveu JSON
    estruturado, porque quem lê a thread é gente. `finding` é `None` enquanto a
    thread não concluiu, e não um achado vazio: achado vazio publicado seria o
    registro durável de nada.
    """

    reply: str
    concluded: bool
    finding: Finding | None
    turn: Turn
    model_reply: Reply
    warnings: tuple[str, ...] = field(default_factory=tuple)


def _texto_de_resposta(resposta: Reply) -> str:
    """A fala para a thread.

    Quando há saída estruturada, é o campo `reply`. Quando não há (o fornecedor
    devolveu texto solto porque o schema falhou, por exemplo), é o texto cru —
    devolver vazio esconderia do humano a única coisa que o modelo produziu.
    """
    dados = resposta.data or {}
    texto = str(dados.get("reply") or "").strip()
    return texto or resposta.text.strip()


def _achado(dados: dict) -> Finding | None:
    titulo = str(dados.get("finding_title") or "").strip()
    resumo = str(dados.get("finding_summary") or "").strip()
    if not titulo or not resumo:
        return None
    evidencias = [str(e) for e in (dados.get("finding_evidence") or [])]
    return Finding(title=titulo, payload={"summary": resumo, "evidence": evidencias})


async def execute_turn(
    provider: AgentProvider,
    turn: Turn,
    *,
    model: str,
    effort: Effort,
) -> TurnExecution:
    """Uma volta: manda a conversa, lê a resposta, decide se concluiu.

    Não retenta e não faz laço de ferramenta — ferramentas estão fora da porta
    nesta entrega (ver `ports.py`). O que este "laço" faz é a volta completa de
    UM turno; o próximo turno é decisão de quem chamou, e é assim que o
    orçamento consegue interrompê-lo entre uma volta e outra (ADR-0011 §2).
    """
    resposta = await provider.send(turn, model=model, effort=effort)
    dados = dict(resposta.data or {})
    avisos = list(resposta.warnings)

    concluiu = bool(dados.get("concluded"))
    achado = _achado(dados) if concluiu else None
    if concluiu and achado is None:
        # Concluir EXIGE achado (spec de conversação §1). Sem ele a conclusão
        # não vale: a thread continua ativa e o agente é cobrado no próximo
        # turno, em vez de a thread sumir do radar com um `true` de enfeite.
        concluiu = False
        avisos.append(
            "o modelo marcou conclusão sem achado (título e resumo): a conclusão "
            "foi RECUSADA — concluir exige publicar achado (spec §1)"
        )

    if resposta.stop_reason is StopReason.MAX_TOKENS:
        # Resposta cortada não é resposta concluída. Sem este aviso, um turno
        # truncado pareceria uma resposta curta.
        avisos.append(
            "a resposta foi CORTADA pelo limite de tokens de saída: o conteúdo "
            "abaixo está incompleto"
        )
    if resposta.stop_reason is StopReason.REFUSED:
        avisos.append("o provedor RECUSOU a solicitação por política própria")
    if not provider.supports(Capability.CACHE_CREATION_ACCOUNTING):
        # ADR-0012 §1: sem esta capacidade, `cache_creation_tokens` vem 0 e
        # isso significa "não dá para saber", não "nada foi escrito no cache".
        avisos.append(
            f"o provedor '{provider.name}' não reporta criação de cache: o campo "
            "cache_creation_tokens vem zerado por AUSÊNCIA de informação (D1)"
        )

    return TurnExecution(
        reply=_texto_de_resposta(resposta),
        concluded=concluiu,
        finding=achado,
        turn=turn,
        model_reply=resposta,
        warnings=tuple(avisos),
    )


__all__ = ["Finding", "TurnExecution", "execute_turn"]
