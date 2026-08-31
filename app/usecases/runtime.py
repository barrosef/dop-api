"""O ciclo do turno com o núcleo — o AgentRuntime visto de fora.

Mesma disciplina de `identity` e `cost`: a regra vive aqui, `app/routers/
runtime.py` traduz HTTP e `app/grpcapi/runtime.py` traduz protobuf, os dois
chamando esta MESMA função. Ver o docstring de `app/usecases/identity.py` para
o porquê dos decorators morarem no caso de uso.

A divisão com `app/runtime/`: lá está tudo o que não toca o núcleo (a porta do
provedor, a montagem do prompt, a conversa com o modelo); aqui está o ciclo com
o núcleo. As duas metades falham por motivos diferentes — fornecedor fora do ar
não é núcleo fora do ar — e é por isso que elas não moram no mesmo arquivo.

═══════════════════════════════════════════════════════════════════════════════
O CICLO, e as decisões que não são óbvias
═══════════════════════════════════════════════════════════════════════════════

**0. O provedor primeiro, antes de qualquer RPC.** Credencial ausente é
descoberta na primeira linha, não depois de montar contexto e gastar duas idas
ao núcleo. O erro que chega ao usuário fala de configuração, que é o que é.

**1. Contexto e threads EM PARALELO** (`gather`), como `demand.get_cockpit`:
as duas chamadas não dependem uma da outra e somar as latências transformaria
a agregação num custo. O pacote já vem cortado por orçamento de tokens
(ADR-0009 §3) e informa o DESCARTE — que não some: entra no prefixo (o agente
precisa saber que lê contexto parcial) e vira mensagem na thread (o humano
precisa saber por que a resposta ficou como ficou).

**2. O roteamento é do núcleo, e a justificativa dele viaja inteira** (ADR-0011
§3). O que a borda faz é a metade que o núcleo não pode fazer: traduzir a
CLASSE para o nome concreto do fornecedor ATIVO — a mesma separação política ×
catálogo do `internal/domain/cost/router.go`. Quando a ficha da thread declara
modelo (ADR-0010 §2), ela vence: ficha é o contrato congelado daquela thread, e
trocar o modelo dela no meio invalidaria o prefixo cacheado de todos os turnos
anteriores (ADR-0012 §1 — cache é por modelo).

**3. Prefixo estável primeiro, volátil depois.** Ver `app/runtime/prompt.py`.

**4. A medição é IDEMPOTENTE, e a chave é derivada do TURNO.** Uma duplicata
de `RecordUsage` não colide com nada: entraria como consumo legítimo e o
orçamento viraria ficção. Todas as escritas deste ciclo derivam da mesma chave
de turno (`:msg-in`, `:notice`, `:usage`, `:msg-out`, `:finding`), de modo que
reenviar a MESMA requisição — com o mesmo `Idempotency-Key` — repete zero
efeitos. Sem chave do cliente, geramos uma: cada chamada passa a ser um turno
NOVO, porque é o cliente quem sabe se está retentando ou perguntando de novo.

**5. Toda mensagem é evento** (ADR-0006) e é assim que o cockpit fica sabendo:
o SSE que já existe (`usecases/stream.py`, `WatchDemand`) entrega os eventos
sozinho. **Não há um segundo caminho de streaming aqui** — de propósito.

**6. Concluir exige publicar achado** (spec §1). A recusa de conclusão sem
achado acontece em `app/runtime/loop.py`; aqui só se publica o que passou.

**7. Orçamento estourado PAUSA, não mata** (ADR-0011 §2). `RecordUsage` avisa;
o turno que já rodou é entregue inteiro — a resposta é publicada e o achado
também —, e o resultado sai com `paused` verdadeiro e o `notice` que a caixa de
atenção mostra. O próximo turno é que não sai. Abortar aqui seria o corte duro
que a ADR recusou, e ainda por cima jogaria fora tokens já pagos.
"""

import asyncio

from pydantic import BaseModel, Field

from app.coreclient.client import core
from app.platform.logging.config import get_logger
from app.platform.logging.decorator import log
from app.platform.security.decorator import account_scoped, require_role
from app.runtime import loop as runtime_loop
from app.runtime import prompt as runtime_prompt
from app.runtime.adapters import provider_for
from app.runtime.catalog import class_of, effort_of
from app.runtime.ports import AgentProvider, Capability, Effort
from app.usecases import cost as cost_uc
from app.usecases import demand as demand_uc
from app.usecases import knowledge as knowledge_uc

# ── modelos da borda ────────────────────────────────────────────────────────


class RunTurn(BaseModel):
    """Um turno a executar numa thread."""

    text: str = Field(min_length=1)
    # Vocabulário ABERTO (o núcleo trata desconhecido caindo no caro e diz que
    # caiu). Vazio é que não passa: sem tipo de trabalho não há roteamento.
    task_kind: str = Field(default="implementation", min_length=1)
    # Vazio = o provedor padrão da instalação. Escolha POR REQUISIÇÃO porque
    # provedor de agente é recurso de conta (ADR-0013), não decisão de boot.
    provider: str = ""
    # Instrução do OPERADOR, vinda da caixa de atenção. Entra pelo canal de
    # autoridade do fornecedor, nunca como texto de usuário (ver D3).
    operator_note: str = ""
    max_output_tokens: int = Field(default=8192, ge=256, le=64000)


class TurnUsage(BaseModel):
    """As quatro parcelas, DISJUNTAS, mais o custo em micros inteiros."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    cost: cost_uc.Money = Field(default_factory=cost_uc.Money)
    # None = o provedor não reporta criação de cache (D1). Zero afirmaria que
    # nada foi escrito no cache, que é outra coisa — a mesma disciplina de
    # `knowledge.ContextPackageSummary.dropped`.
    cache_creation_known: bool = True
    # None = não há tabela de preço para este modelo. O custo NÃO vira zero de
    # consolo: um orçamento alimentado com zeros é a ficção da ADR-0011 §2.
    cost_known: bool = True


class RoutingView(BaseModel):
    """A decisão do núcleo, com a justificativa INTEIRA (ADR-0011 §3)."""

    task_kind: str = ""
    model: str = ""
    effort: str = ""
    effort_applied: str = ""
    reason: str = ""
    # Verdadeiro quando a ficha da thread (ADR-0010 §2) venceu o roteador.
    from_agent_card: bool = False


class FindingRef(BaseModel):
    id: str = ""
    title: str = ""


class TurnOutcome(BaseModel):
    demand_id: str
    thread_id: str
    provider: str = ""
    routing: RoutingView = Field(default_factory=RoutingView)
    reply: str = ""
    # Ids das mensagens publicadas na thread, na ordem em que entraram.
    message_ids: list[str] = Field(default_factory=list)
    concluded: bool = False
    finding: FindingRef | None = None
    usage: TurnUsage = Field(default_factory=TurnUsage)
    # O contexto veio truncado (ADR-0012). Aparece na conversa, não só aqui.
    context_truncated: bool = False
    # Orçamento estourado: a demanda PAUSA e vira item de decisão (ADR-0011 §2).
    paused: bool = False
    notice: str = ""
    budgets: list[cost_uc.BudgetView] = Field(default_factory=list)
    # Avisos legíveis: effort rebaixado, resposta cortada, conclusão recusada…
    warnings: list[str] = Field(default_factory=list)


# ── auxiliares ──────────────────────────────────────────────────────────────


def _chaves(turn_key: str) -> dict[str, str]:
    """As chaves de idempotência das escritas deste turno, todas derivadas.

    Derivadas e não sorteadas: é o que faz reenviar a mesma requisição repetir
    ZERO efeitos — a mensagem não duplica, o consumo não conta duas vezes e o
    achado não é publicado de novo.
    """
    return {
        alvo: f"{turn_key}:{alvo}"
        for alvo in ("msg-in", "notice", "usage", "msg-out", "finding")
    }


def _thread(threads: list[demand_uc.Thread], thread_id: str) -> demand_uc.Thread | None:
    return next((t for t in threads if t.id == thread_id), None)


def _modelo_e_effort(
    provider: AgentProvider,
    decisao: cost_uc.RoutingDecision,
    card: demand_uc.AgentCard | None,
) -> tuple[str, Effort, bool]:
    """Decisão do núcleo + ficha da thread → (modelo concreto, effort, ficha?).

    A tradução que só a borda pode fazer: o núcleo devolve o nome do catálogo
    DELE (`claude-opus`), e o fornecedor ativo tem outro (`claude-opus-5`, ou
    algo completamente diferente se for outro fornecedor). Quando a classe é
    reconhecível, resolvemos pelo catálogo do adaptador; quando não é, o nome
    passa INTACTO — adivinhar a classe de um nome desconhecido trocaria em
    silêncio o modelo que o núcleo escolheu.
    """
    da_ficha = bool(card and card.model)
    nome = (card.model if da_ficha and card else "") or decisao.model
    classe = class_of(nome)
    modelo = provider.resolve_model(classe) if classe is not None else nome
    esforco = effort_of((card.effort if da_ficha and card else "") or decisao.effort)
    return modelo, esforco, da_ficha


# ── caso de uso ─────────────────────────────────────────────────────────────


@log
@account_scoped
@require_role("owner", "admin", "developer")
async def run_turn(
    demand_id: str,
    thread_id: str,
    body: RunTurn,
    idempotency_key: str = "",
    *,
    provider: AgentProvider | None = None,
) -> TurnOutcome:
    """Executa UM turno numa thread. Ver o ciclo no docstring do módulo.

    `provider` injetável é a porta de entrada do teste e, mais adiante, do dia
    em que o provedor vier do recurso da conta (ADR-0013) em vez do ambiente:
    quem monta o adaptador passa a ser o chamador, e este ciclo não muda.
    """
    registro = get_logger()
    # 0. Provedor ANTES de qualquer RPC: credencial ausente falha aqui, barato.
    ativo = provider if provider is not None else provider_for(body.provider)
    chaves = _chaves(idempotency_key or core.idempotency_key())

    # 1. Contexto e threads em paralelo.
    pacote, threads = await asyncio.gather(
        knowledge_uc.get_context_package(demand_id),
        demand_uc.list_threads(demand_id),
    )
    thread = _thread(threads, thread_id)
    card = thread.card if thread else None

    # 2. Roteamento — decisão do núcleo, com a justificativa inteira.
    decisao = await cost_uc.route_model(body.task_kind, demand_id)
    modelo, esforco, da_ficha = _modelo_e_effort(ativo, decisao, card)
    registro.info(
        "roteamento do turno",
        provider=ativo.name,
        model=modelo,
        effort=esforco.value,
        task_kind=decisao.task_kind,
        from_agent_card=da_ficha,
        # A justificativa vai INTEIRA para o log: é o que permite auditar
        # "por que esta demanda rodou no modelo caro?" (ADR-0011 §3).
        routing_reason=decisao.reason,
    )

    # A pergunta entra na thread ANTES da chamada ao modelo: se o fornecedor
    # cair, a conversa mostra o que foi perguntado em vez de um buraco.
    ids: list[str] = []
    entrada = await demand_uc.post_message(
        thread_id, demand_uc.NewMessage(text=body.text), chaves["msg-in"]
    )
    ids.append(entrada.id)

    aviso_truncamento = runtime_prompt.truncation_notice(pacote)
    if aviso_truncamento:
        # O truncamento não some: o humano que lê a thread precisa saber que a
        # resposta abaixo foi produzida sem parte do contexto.
        nota = await demand_uc.post_message(
            thread_id, demand_uc.NewMessage(text=aviso_truncamento), chaves["notice"]
        )
        ids.append(nota.id)

    # 3. Prefixo estável primeiro, volátil depois (ADR-0012 §1).
    turno = runtime_prompt.build_turn(
        pacote=pacote,
        thread_key=thread.key if thread else "",
        card=card,
        text=body.text,
        operator_note=body.operator_note,
        max_output_tokens=body.max_output_tokens,
    )
    execucao = await runtime_loop.execute_turn(
        ativo, turno, model=modelo, effort=esforco
    )
    resposta = execucao.model_reply
    avisos = list(execucao.warnings)

    # 4. Medição, idempotente e com os campos de cache (ADR-0011 §4).
    preco = ativo.price_for(resposta.model or modelo)
    if preco is None:
        avisos.append(
            f"sem tabela de preço para '{resposta.model or modelo}' em "
            f"'{ativo.name}': o consumo foi registrado em tokens, e o CUSTO "
            "ficou zerado por AUSÊNCIA de tabela — não por ser de graça"
        )
    consumo = await cost_uc.record_usage(
        cost_uc.NewUsage(
            model=resposta.model or modelo,
            demand_id=demand_id,
            thread_id=thread_id,
            input_tokens=resposta.usage.input_tokens,
            output_tokens=resposta.usage.output_tokens,
            cache_read_tokens=resposta.usage.cache_read_tokens,
            cache_creation_tokens=resposta.usage.cache_creation_tokens,
            cost_micros=preco.cost_micros(resposta.usage) if preco else 0,
            currency=preco.currency if preco else "",
        ),
        chaves["usage"],
    )

    # 5. A resposta na thread. Toda mensagem é evento (ADR-0006) — é por aí que
    # o SSE do cockpit fica sabendo, sem um segundo caminho de streaming.
    # `actor_kind="agent"`: a autoria no log é do AGENTE, não de quem apertou
    # o botão. A entrada acima continua sendo do humano — é a distinção que a
    # plataforma inteira existe para manter.
    saida = await demand_uc.post_message(
        thread_id,
        demand_uc.NewMessage(text=execucao.reply),
        chaves["msg-out"],
        actor_kind="agent",
    )
    ids.append(saida.id)

    # 6. Concluir exige publicar achado (spec §1).
    achado_ref = None
    if execucao.concluded and execucao.finding is not None:
        publicado = await demand_uc.publish_finding(
            demand_id,
            demand_uc.NewFinding(
                thread_id=thread_id,
                title=execucao.finding.title,
                # A proveniência entra no achado: quem auditar precisa saber
                # com que modelo e sob que política ele foi produzido.
                payload={
                    **execucao.finding.payload,
                    "provider": ativo.name,
                    "model": resposta.model or modelo,
                    "effort": resposta.effort_applied.value,
                    "routing_reason": decisao.reason,
                },
            ),
            chaves["finding"],
        )
        achado_ref = FindingRef(id=publicado.id, title=publicado.title)

    # 7. Estouro de orçamento: PAUSA e vira item de decisão. O turno que já
    # rodou continua entregue — abortar aqui jogaria fora tokens já pagos.
    if consumo.budget_exceeded:
        registro.info(
            "demanda pausada por orçamento",
            demand_id=demand_id,
            thread_id=thread_id,
            model=resposta.model or modelo,
        )

    return TurnOutcome(
        demand_id=demand_id,
        thread_id=thread_id,
        provider=ativo.name,
        routing=RoutingView(
            task_kind=decisao.task_kind,
            model=resposta.model or modelo,
            effort=esforco.value,
            effort_applied=resposta.effort_applied.value,
            reason=decisao.reason,
            from_agent_card=da_ficha,
        ),
        reply=execucao.reply,
        message_ids=ids,
        concluded=execucao.concluded,
        finding=achado_ref,
        usage=TurnUsage(
            input_tokens=resposta.usage.input_tokens,
            output_tokens=resposta.usage.output_tokens,
            cache_read_tokens=resposta.usage.cache_read_tokens,
            cache_creation_tokens=resposta.usage.cache_creation_tokens,
            cost=cost_uc.Money(
                currency=preco.currency if preco else "",
                amount_micros=preco.cost_micros(resposta.usage) if preco else 0,
            ),
            cache_creation_known=ativo.supports(Capability.CACHE_CREATION_ACCOUNTING),
            cost_known=preco is not None,
        ),
        context_truncated=bool(aviso_truncamento),
        paused=consumo.budget_exceeded,
        notice=consumo.notice,
        budgets=consumo.budgets,
        warnings=avisos,
    )


__all__ = [
    "FindingRef",
    "RoutingView",
    "RunTurn",
    "TurnOutcome",
    "TurnUsage",
    "run_turn",
]
