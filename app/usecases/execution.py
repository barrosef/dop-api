"""Execution substrate use cases — the demand's sandbox.

The same discipline as `identity` and `hierarchy`: the rule lives here, the
router and the servicer translate. See `app/usecases/identity.py`'s docstring
for why the decorators live in the use case and not in the adapter.

**This module's hard rule: `min_tier` is DECLARED, never presumed.**

There is no default for the isolation level in this file, and there must not
come to be one — neither "namespace because it is what always works" nor "the
last one the account used". A default here would be the platform choosing the
isolation of code it did not write, and choosing downwards: whoever asked for a
microVM and got a container does not find that out from the screen, they find it
out from the incident. The core refuses an undeclared level; the edge refuses
BEFORE, with a message, because spending a round trip to the core to be told
"you did not say" is waste and the message arrives the same.

**Destroying is irreversible and takes the workspace with it.** `suspend` kills
the execution and preserves the workspace on the PVC (it is the saving
operation); `destroy` erases both, and a destroyed sandbox does not resume — the
way forward is to provision another, from scratch. That is why `destroy_sandbox`
returns a confirmation rather than silence.
"""

from datetime import UTC, datetime

from pydantic import BaseModel, Field

from app.coreclient import stubs
from app.coreclient.client import core
from app.coreclient.gen.dop.v1 import execution_pb2
from app.platform.logging.decorator import log
from app.platform.security.decorator import account_scoped, require_role
from app.settings import settings

# Name ↔ enum in one place only. The names are the core's
# (execution.RequireTier: "hardware, kernel_emulated or namespace"); the
# substrate spec writes `kernel-emulated` with a hyphen in its environment
# table, but the vocabulary that crosses the boundary is the core's — two
# spellings for the same value is a translation bug waiting to happen.
_TIER_BY_NAME = {
    "hardware": execution_pb2.ISOLATION_TIER_HARDWARE,
    "kernel_emulated": execution_pb2.ISOLATION_TIER_KERNEL_EMULATED,
    "namespace": execution_pb2.ISOLATION_TIER_NAMESPACE,
}
_NAME_BY_TIER = {v: k for k, v in _TIER_BY_NAME.items()}

# UNSPECIFIED deliberately OUTSIDE the dictionary: it is not a value the edge
# accepts, it is the absence of a value.
_TIERS = "^(hardware|kernel_emulated|namespace)$"

_STATE_BY_ENUM = {
    execution_pb2.Sandbox.STATE_PROVISIONING: "provisioning",
    execution_pb2.Sandbox.STATE_ACTIVE: "active",
    execution_pb2.Sandbox.STATE_SUSPENDED: "suspended",
    execution_pb2.Sandbox.STATE_DESTROYED: "destroyed",
}

# Every role EXCEPT viewer. The core refuses a viewer on provisioning,
# suspending, resuming and destroying; the edge refuses first, and refuses the
# same on both ports because the decorator sits in the use case. Describing the
# rule by the roles that MAY (and not by who may not) is what `require_role`
# offers — there are four fixed roles, so the list is closed.
_CHANGES_LIFE_CYCLE = ("owner", "admin", "developer")


def _deadline() -> float:
    return settings.core_deadline_s


def _idempotency(key: str = "") -> str:
    return key or core.idempotency_key()


# ── the edge's models ───────────────────────────────────────────────────────


class EndpointSummary(BaseModel):
    name: str
    url: str = ""
    port: int = 0
    state: str = ""  # running | stopped


class SandboxSummary(BaseModel):
    id: str
    demand_id: str = ""
    state: str = ""
    # What the substrate DELIVERED — the client sees what it got. Never a
    # promise: the core discards the sandbox if the adapter delivers another
    # level.
    tier: str = ""
    namespace: str = ""
    endpoints: list[EndpointSummary] = Field(default_factory=list)
    # None = no activity recorded. It does not become epoch zero: "idle since
    # 1970" would make the automatic suspension read it wrong.
    last_active_at: datetime | None = None


class NewSandbox(BaseModel):
    """A provisioning request.

    `min_tier` has NO default, and that is what makes the rule hold: an optional
    field with a default value would be the presumption coming back in through
    the back door. Without it, validation refuses right here — a 422 in REST,
    INVALID_ARGUMENT in gRPC — and the core is never even called.
    """

    demand_id: str = Field(min_length=1)
    min_tier: str = Field(pattern=_TIERS)


class DestroyResult(BaseModel):
    """The confirmation of an irreversible act.

    It exists as a model (and not as `None`) because destroying erases the
    execution AND the workspace: a mute 204 would force the client to deduce what
    happened, and what happened has no way back.
    """

    destroyed: bool


# ── translating the core into the edge ──────────────────────────────────────


def _sandbox(s: execution_pb2.Sandbox) -> SandboxSummary:
    return SandboxSummary(
        id=s.id,
        demand_id=s.demand.id,
        state=_STATE_BY_ENUM.get(s.state, ""),
        tier=_NAME_BY_TIER.get(s.tier, ""),
        namespace=s.namespace,
        endpoints=[
            EndpointSummary(name=e.name, url=e.url, port=e.port, state=e.state) for e in s.endpoints
        ],
        # HasField because a timestamp is a MESSAGE field: absent ≠ zeroed.
        last_active_at=(
            s.last_active_at.ToDatetime(tzinfo=UTC) if s.HasField("last_active_at") else None
        ),
    )


# ── use cases ───────────────────────────────────────────────────────────────


@log
@account_scoped
@require_role(*_CHANGES_LIFE_CYCLE)
async def provision_sandbox(body: NewSandbox, idempotency_key: str = "") -> SandboxSummary:
    """Creates the demand's sandbox, at the DECLARED isolation level.

    `min_tier` goes down as the client declared it. The edge does not complete
    it, does not downgrade it when the substrate does not offer the requested
    level (that is the core's refusal, with a message — silent degradation is
    the failure mode the substrate spec §2 forbids) and does not promote it "to
    be safe": promoting is also choosing for the client, and it is the client
    who answers for the cost.
    """
    s = await stubs.execution_stub().ProvisionSandbox(
        execution_pb2.ProvisionSandboxRequest(
            demand_id=body.demand_id,
            min_tier=_TIER_BY_NAME[body.min_tier],
            idempotency_key=_idempotency(idempotency_key),
        ),
        metadata=core.metadata(),
        timeout=_deadline(),
    )
    return _sandbox(s)


@log
@account_scoped
async def describe_sandbox(sandbox_id: str) -> SandboxSummary:
    """The sandbox as it IS — each endpoint's state included.

    A read requires no role beyond an active account: seeing which isolation
    level the demand is running at is precisely what the spec wants to be
    visible.
    """
    s = await stubs.execution_stub().DescribeSandbox(
        execution_pb2.DescribeSandboxRequest(id=sandbox_id),
        metadata=core.metadata(),
        timeout=_deadline(),
    )
    return _sandbox(s)


@log
@account_scoped
@require_role(*_CHANGES_LIFE_CYCLE)
async def suspend_sandbox(sandbox_id: str) -> SandboxSummary:
    """Kills the execution and PRESERVES the workspace — the saving operation.

    Repeating is harmless: suspending what is already suspended returns the
    sandbox as it stands, without touching the substrate and without emitting an
    event.
    """
    s = await stubs.execution_stub().SuspendSandbox(
        execution_pb2.SuspendSandboxRequest(id=sandbox_id),
        metadata=core.metadata(),
        timeout=_deadline(),
    )
    return _sandbox(s)


@log
@account_scoped
@require_role(*_CHANGES_LIFE_CYCLE)
async def resume_sandbox(sandbox_id: str) -> SandboxSummary:
    """Recreates the execution ON TOP OF the existing workspace.

    A destroyed sandbox does not resume, and the refusal comes from the core with
    the reason written out ("the destruction takes the workspace with it"). The
    edge passes it on: turning that into an "automatically provision another"
    would hide from the dev that they lost the workspace.
    """
    s = await stubs.execution_stub().ResumeSandbox(
        execution_pb2.ResumeSandboxRequest(id=sandbox_id),
        metadata=core.metadata(),
        timeout=_deadline(),
    )
    return _sandbox(s)


@log
@account_scoped
@require_role(*_CHANGES_LIFE_CYCLE)
async def destroy_sandbox(sandbox_id: str) -> DestroyResult:
    """IRREVERSIBLE: it erases the execution AND the demand's workspace.

    It is not the inverse of `suspend_sandbox`. What is lost here is the
    branches' worktree, the build already done and everything the agent had on
    disk; resuming stops being possible and the only way forward becomes
    provisioning a new sandbox, from scratch. Whoever wants to save resources
    wants `suspend`.

    Idempotent by state, as in the core: destroying what was already destroyed
    returns `destroyed=true` without touching anything — whoever repeats the call
    wants the same result, and the result is already there.
    """
    resp = await stubs.execution_stub().DestroySandbox(
        execution_pb2.DestroySandboxRequest(id=sandbox_id),
        metadata=core.metadata(),
        timeout=_deadline(),
    )
    return DestroyResult(destroyed=resp.destroyed)
