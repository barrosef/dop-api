"""Servicer gRPC de recursos — adaptador protobuf sobre os casos de uso.

Nenhuma decisão aqui. Em particular: nenhum caminho que devolva o segredo —
`SetCredential` responde o recurso, e o recurso não carrega valor nenhum.
"""

import base64

from google.protobuf import struct_pb2

from app.grpcapi.gen.dop.bff.v1 import resource_pb2 as bff
from app.grpcapi.gen.dop.bff.v1 import resource_pb2_grpc as bff_grpc
from app.usecases import resource as uc

# Enum da borda ↔ nome do caso de uso. Os números são os mesmos de
# dop.v1.Resource.Kind, então a conversão é identidade, não tradução.
_NOME_POR_ENUM = {
    bff.RESOURCE_KIND_INTEGRATION: "integration",
    bff.RESOURCE_KIND_SKILL: "skill",
    bff.RESOURCE_KIND_WORKFLOW: "workflow",
    bff.RESOURCE_KIND_GIT_FLOW: "git_flow",
}
_ENUM_POR_NOME = {v: k for k, v in _NOME_POR_ENUM.items()}


def _resource(r: uc.ResourceSummary) -> bff.Resource:
    config = struct_pb2.Struct()
    config.update(r.config)
    return bff.Resource(
        id=r.id,
        kind=_ENUM_POR_NOME.get(r.kind, bff.RESOURCE_KIND_UNSPECIFIED),
        name=r.name,
        version=r.version,
        config=config,
        credential_ref=r.credential_ref,
    )


class ResourceServicer(bff_grpc.ResourceServiceServicer):
    async def ListResources(
        self, request: bff.ListResourcesRequest, context
    ) -> bff.ListResourcesResponse:
        kind = _NOME_POR_ENUM.get(request.kind, "")
        return bff.ListResourcesResponse(
            resources=[_resource(r) for r in await uc.list_resources(kind)]
        )

    async def GetResource(self, request: bff.GetResourceRequest, context) -> bff.Resource:
        return _resource(await uc.get_resource(request.id))

    async def CreateResource(
        self, request: bff.CreateResourceRequest, context
    ) -> bff.Resource:
        body = uc.NewResource(
            kind=_NOME_POR_ENUM.get(request.kind, ""),
            name=request.name,
            config=dict(request.config) if request.HasField("config") else {},
        )
        return _resource(await uc.create_resource(body, request.idempotency_key))

    async def SetCredential(
        self, request: bff.SetCredentialRequest, context
    ) -> bff.Resource:
        # O caso de uso recebe base64 porque é assim que o REST o entrega; aqui
        # os bytes já vêm crus e voltam a ser codificados. Uma assinatura só
        # para os dois transportes vale o reencode.
        body = uc.NewCredential(secret_base64=base64.b64encode(request.secret).decode())
        return _resource(await uc.set_credential(request.resource_id, body))

    async def GrantResource(
        self, request: bff.GrantResourceRequest, context
    ) -> bff.Grant:
        g = await uc.grant_resource(
            uc.NewGrant(
                resource_id=request.resource_id,
                user_id=request.user_id,
                level=request.level,
            )
        )
        return bff.Grant(
            id=g.id, resource_id=g.resource_id, user_id=g.user_id, level=g.level
        )

    async def RevokeGrant(
        self, request: bff.RevokeGrantRequest, context
    ) -> bff.RevokeGrantResponse:
        return bff.RevokeGrantResponse(revoked=await uc.revoke_grant(request.grant_id))
