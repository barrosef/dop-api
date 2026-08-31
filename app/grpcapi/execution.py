"""Servicer gRPC do substrato — adaptador protobuf sobre os casos de uso.

Simétrico a `app/routers/execution.py`: recebe mensagem, chama a MESMA função
de `app/usecases/execution.py`, devolve mensagem. Nenhuma decisão aqui.

E, em particular, **nenhum default de isolamento**: `ISOLATION_TIER_UNSPECIFIED`
vira string vazia, o modelo do caso de uso a recusa e o cliente recebe
INVALID_ARGUMENT. Preencher o vazio "para o cliente não precisar pensar" seria
o servicer decidindo o isolamento — exatamente o que a spec do substrato §2
proíbe, e da forma mais difícil de auditar: em silêncio.

`StreamLogs` não está aqui de propósito — o streaming da borda está sendo
desenhado em separado.
"""

from app.grpcapi.gen.dop.bff.v1 import execution_pb2 as bff
from app.grpcapi.gen.dop.bff.v1 import execution_pb2_grpc as bff_grpc
from app.usecases import execution as uc

# Enum da borda ↔ nome do caso de uso. Os números são os mesmos de
# dop.v1.IsolationTier, então a conversão é identidade, não tradução.
_NOME_POR_TIER = {
    bff.ISOLATION_TIER_HARDWARE: "hardware",
    bff.ISOLATION_TIER_KERNEL_EMULATED: "kernel_emulated",
    bff.ISOLATION_TIER_NAMESPACE: "namespace",
}
_TIER_POR_NOME = {v: k for k, v in _NOME_POR_TIER.items()}

_ESTADO_POR_NOME = {
    "provisioning": bff.Sandbox.STATE_PROVISIONING,
    "active": bff.Sandbox.STATE_ACTIVE,
    "suspended": bff.Sandbox.STATE_SUSPENDED,
    "destroyed": bff.Sandbox.STATE_DESTROYED,
}


def _sandbox(s: uc.SandboxSummary) -> bff.Sandbox:
    msg = bff.Sandbox(
        id=s.id,
        demand_id=s.demand_id,
        state=_ESTADO_POR_NOME.get(s.state, bff.Sandbox.STATE_UNSPECIFIED),
        tier=_TIER_POR_NOME.get(s.tier, bff.ISOLATION_TIER_UNSPECIFIED),
        namespace=s.namespace,
        endpoints=[
            bff.SandboxEndpoint(name=e.name, url=e.url, port=e.port, state=e.state)
            for e in s.endpoints
        ],
    )
    # Sem atividade registrada fica sem: o zero do protobuf é 1970, e a
    # suspensão automática lê justamente este campo.
    if s.last_active_at is not None:
        msg.last_active_at.FromDatetime(s.last_active_at)
    return msg


class ExecutionServicer(bff_grpc.ExecutionServiceServicer):
    async def ProvisionSandbox(
        self, request: bff.ProvisionSandboxRequest, context
    ) -> bff.Sandbox:
        body = uc.NewSandbox(
            demand_id=request.demand_id,
            # UNSPECIFIED vira "" e o modelo recusa. É o `get` sem default que
            # mantém a regra viva: um `.get(x, "namespace")` aqui seria a
            # presunção entrando pela porta dos fundos.
            min_tier=_NOME_POR_TIER.get(request.min_tier, ""),
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
