"""The executor gRPC servicer — a protobuf adapter over the use cases.

Symmetric to `app/routers/execution.py`: it receives a message, calls the SAME
function from `app/usecases/execution.py`, returns a message. No decisions here.

And, in particular, **no isolation default**: `ISOLATION_TIER_UNSPECIFIED`
becomes an empty string, the use case's model refuses it and the client receives
INVALID_ARGUMENT. Filling the gap "so the client does not have to think" would
be the servicer deciding the isolation — exactly what the execution spec §2
forbids, and in the hardest way to audit: in silence.

`StreamLogs` is deliberately not here — the edge's streaming is being designed
separately.
"""

from app.grpcapi.gen.dop.bff.v1 import execution_pb2 as bff
from app.grpcapi.gen.dop.bff.v1 import execution_pb2_grpc as bff_grpc
from app.usecases import execution as uc

# The edge's enum ↔ the use case's name. The numbers are the same as
# dop.v1.IsolationTier's, so the conversion is the identity, not a translation.
_NAME_BY_TIER = {
    bff.ISOLATION_TIER_HARDWARE: "hardware",
    bff.ISOLATION_TIER_KERNEL_EMULATED: "kernel_emulated",
    bff.ISOLATION_TIER_NAMESPACE: "namespace",
}
_TIER_BY_NAME = {v: k for k, v in _NAME_BY_TIER.items()}

_STATE_BY_NAME = {
    "provisioning": bff.Sandbox.STATE_PROVISIONING,
    "active": bff.Sandbox.STATE_ACTIVE,
    "suspended": bff.Sandbox.STATE_SUSPENDED,
    "destroyed": bff.Sandbox.STATE_DESTROYED,
}


def _sandbox(s: uc.SandboxSummary) -> bff.Sandbox:
    msg = bff.Sandbox(
        id=s.id,
        demand_id=s.demand_id,
        state=_STATE_BY_NAME.get(s.state, bff.Sandbox.STATE_UNSPECIFIED),
        tier=_TIER_BY_NAME.get(s.tier, bff.ISOLATION_TIER_UNSPECIFIED),
        namespace=s.namespace,
        endpoints=[
            bff.SandboxEndpoint(name=e.name, url=e.url, port=e.port, state=e.state)
            for e in s.endpoints
        ],
    )
    # With no activity recorded it stays without: protobuf's zero is 1970, and
    # the automatic suspension reads precisely this field.
    if s.last_active_at is not None:
        msg.last_active_at.FromDatetime(s.last_active_at)
    return msg


class ExecutionServicer(bff_grpc.ExecutionServiceServicer):
    async def ProvisionSandbox(
        self, request: bff.ProvisionSandboxRequest, context
    ) -> bff.Sandbox:
        body = uc.NewSandbox(
            demand_id=request.demand_id,
            # UNSPECIFIED becomes "" and the model refuses. It is the `get` with
            # no default that keeps the rule alive: a `.get(x, "namespace")` here
            # would be the presumption coming in through the back door.
            min_tier=_NAME_BY_TIER.get(request.min_tier, ""),
        )
        return _sandbox(await uc.provision_sandbox(body, request.idempotency_key))

    async def DescribeSandbox(
        self, request: bff.DescribeSandboxRequest, context
    ) -> bff.Sandbox:
        return _sandbox(await uc.describe_sandbox(request.id))

    async def SuspendSandbox(
        self, request: bff.SuspendSandboxRequest, context
    ) -> bff.Sandbox:
        return _sandbox(await uc.suspend_sandbox(request.id))

    async def ResumeSandbox(
        self, request: bff.ResumeSandboxRequest, context
    ) -> bff.Sandbox:
        return _sandbox(await uc.resume_sandbox(request.id))

    async def DestroySandbox(
        self, request: bff.DestroySandboxRequest, context
    ) -> bff.DestroySandboxResponse:
        resultado = await uc.destroy_sandbox(request.id)
        return bff.DestroySandboxResponse(destroyed=resultado.destroyed)
