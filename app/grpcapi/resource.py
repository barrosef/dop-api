"""The resource gRPC servicer — a protobuf adapter over the use cases.

No decisions here. In particular: no path that returns the secret —
`SetCredential` answers with the resource, and the resource carries no value.
"""

import base64

from google.protobuf import struct_pb2

from app.grpcapi.gen.dop.bff.v1 import resource_pb2 as bff
from app.grpcapi.gen.dop.bff.v1 import resource_pb2_grpc as bff_grpc
from app.usecases import resource as uc

# The edge's enum ↔ the use case's name. The numbers are the same as
# dop.v1.Resource.Kind's, so the conversion is the identity, not a translation.
_NAME_BY_ENUM = {
    bff.RESOURCE_KIND_INTEGRATION: "integration",
    bff.RESOURCE_KIND_SKILL: "skill",
    bff.RESOURCE_KIND_WORKFLOW: "workflow",
    bff.RESOURCE_KIND_GIT_FLOW: "git_flow",
}
_ENUM_BY_NAME = {v: k for k, v in _NAME_BY_ENUM.items()}


def _resource(r: uc.ResourceSummary) -> bff.Resource:
    config = struct_pb2.Struct()
    config.update(r.config)
    return bff.Resource(
        id=r.id,
        kind=_ENUM_BY_NAME.get(r.kind, bff.RESOURCE_KIND_UNSPECIFIED),
        name=r.name,
        version=r.version,
        config=config,
        credential_ref=r.credential_ref,
    )


class ResourceServicer(bff_grpc.ResourceServiceServicer):
    async def ListResources(
        self, request: bff.ListResourcesRequest, context
    ) -> bff.ListResourcesResponse:
        kind = _NAME_BY_ENUM.get(request.kind, "")
        return bff.ListResourcesResponse(
            resources=[_resource(r) for r in await uc.list_resources(kind)]
        )

    async def GetResource(self, request: bff.GetResourceRequest, context) -> bff.Resource:
        return _resource(await uc.get_resource(request.id))

    async def CreateResource(
        self, request: bff.CreateResourceRequest, context
    ) -> bff.Resource:
        body = uc.NewResource(
            kind=_NAME_BY_ENUM.get(request.kind, ""),
            name=request.name,
            config=dict(request.config) if request.HasField("config") else {},
        )
        return _resource(await uc.create_resource(body, request.idempotency_key))

    async def SetCredential(
        self, request: bff.SetCredentialRequest, context
    ) -> bff.Resource:
        # The use case takes base64 because that is how REST delivers it; here
        # the bytes already arrive raw and are encoded again. A single signature
        # for both transports is worth the re-encode.
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
