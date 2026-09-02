"""The execution substrate's routes — the use cases' HTTP translation, nothing more.

Zero decisions here, authorization included: the decorators are in
`app/usecases/execution.py`, and that is why the gRPC port is born with the same
rules — the one that the isolation level is declared, never presumed, included.
"""

from fastapi import APIRouter, Header

from app.usecases import execution as uc
from app.usecases.execution import (
    DestroyResult,
    EndpointSummary,
    NewSandbox,
    SandboxSummary,
)

router = APIRouter(prefix="/api/v1", tags=["execution"])

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
    """Provisions the demand's sandbox.

    **`min_tier` is MANDATORY** — `hardware`, `kernel_emulated` or `namespace`.
    Omitting it is a 422, and it is a 422 on purpose: the platform does not
    choose the isolation of code it did not write, and a default would choose
    downwards. If the substrate does not offer the requested level, the core
    REFUSES with a message — it never silently delivers a lower one.

    The response's `tier` is what the substrate DELIVERED, not what was asked
    for.
    """
    return await uc.provision_sandbox(body, idempotency_key)


@router.get("/sandboxes/{sandbox_id}", response_model=SandboxSummary)
async def describe_sandbox(sandbox_id: str) -> SandboxSummary:
    """The sandbox as it is, with each endpoint's current state."""
    return await uc.describe_sandbox(sandbox_id)


@router.post("/sandboxes/{sandbox_id}/suspend", response_model=SandboxSummary)
async def suspend_sandbox(sandbox_id: str) -> SandboxSummary:
    """Suspends: it kills the execution and PRESERVES the workspace. It is the saving."""
    return await uc.suspend_sandbox(sandbox_id)


@router.post("/sandboxes/{sandbox_id}/resume", response_model=SandboxSummary)
async def resume_sandbox(sandbox_id: str) -> SandboxSummary:
    """Resumes on top of the existing workspace. A destroyed sandbox does not resume."""
    return await uc.resume_sandbox(sandbox_id)


@router.delete("/sandboxes/{sandbox_id}", response_model=DestroyResult)
async def destroy_sandbox(sandbox_id: str) -> DestroyResult:
    """**IRREVERSIBLE: it erases the execution AND the demand's workspace.**

    It is not the inverse of `/suspend`. What is lost here is the branches'
    worktree, the build already done and everything the agent had on disk — and a
    destroyed sandbox does NOT resume: the only way forward becomes provisioning
    another, from scratch. To save resources without losing the work, use
    `/suspend`.

    It returns 200 with `{"destroyed": true}` rather than a 204: an act with no
    way back deserves a confirmation the client can show, not a silence it has to
    deduce.
    """
    return await uc.destroy_sandbox(sandbox_id)
