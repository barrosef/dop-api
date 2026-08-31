"""Rotas do substrato de execução — tradução HTTP dos casos de uso, nada mais.

Zero decisão aqui, autorização inclusa: os decorators estão em
`app/usecases/execution.py`, e é por isso que a porta gRPC nasce com as mesmas
regras — inclusive a de que o nível de isolamento é declarado, nunca presumido.
"""

from fastapi import APIRouter, Header

from app.usecases import execution as uc
from app.usecases.execution import (
    DestroyResult,
    EndpointSummary,
    NewSandbox,
    SandboxSummary,
)

router = APIRouter(prefix="/api/v1", tags=["execução"])

__all__ = [
    "DestroyResult",
    "EndpointSummary",
    "NewSandbox",
    "SandboxSummary",
    "router",
]


@router.post("/sandboxes", response_model=SandboxSummary, status_code=201)
async def provision_sandbox(
    body: NewSandbox,
    idempotency_key: str = Header(default="", alias="Idempotency-Key"),
) -> SandboxSummary:
    """Provisiona o sandbox da demanda.

    **`min_tier` é OBRIGATÓRIO** — `hardware`, `kernel_emulated` ou
    `namespace`. Omiti-lo é 422, e é 422 de propósito: a plataforma não escolhe
    o isolamento de um código que não escreveu, e um default escolheria para
    baixo. Se o substrato não oferecer o nível pedido, o núcleo RECUSA com
    mensagem — nunca entrega um nível menor em silêncio.

    O `tier` da resposta é o que o substrato ENTREGOU, não o que foi pedido.
    """
    return await uc.provision_sandbox(body, idempotency_key)


@router.get("/sandboxes/{sandbox_id}", response_model=SandboxSummary)
async def describe_sandbox(sandbox_id: str) -> SandboxSummary:
    """O sandbox como ele está, com o estado corrente de cada endpoint."""
    return await uc.describe_sandbox(sandbox_id)


@router.post("/sandboxes/{sandbox_id}/suspend", response_model=SandboxSummary)
async def suspend_sandbox(sandbox_id: str) -> SandboxSummary:
    """Suspende: mata a execução e PRESERVA o workspace. É a economia."""
    return await uc.suspend_sandbox(sandbox_id)


@router.post("/sandboxes/{sandbox_id}/resume", response_model=SandboxSummary)
async def resume_sandbox(sandbox_id: str) -> SandboxSummary:
    """Retoma sobre o workspace existente. Sandbox destruído não retoma."""
    return await uc.resume_sandbox(sandbox_id)


@router.delete("/sandboxes/{sandbox_id}", response_model=DestroyResult)
async def destroy_sandbox(sandbox_id: str) -> DestroyResult:
    """**IRREVERSÍVEL: apaga a execução E o workspace da demanda.**

    Não é o inverso de `/suspend`. O que se perde aqui é o worktree das
    branches, o build já feito e tudo que o agente tinha no disco — e sandbox
    destruído NÃO retoma: o único caminho passa a ser provisionar outro, do
    zero. Para economizar recurso sem perder o trabalho, use `/suspend`.

    Devolve 200 com `{"destroyed": true}` em vez de 204: um ato sem volta
    merece uma confirmação que o cliente possa mostrar, não um silêncio que ele
    precise deduzir.
    """
    return await uc.destroy_sandbox(sandbox_id)
