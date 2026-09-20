"""The catalog gRPC servicer — a protobuf adapter over `app/usecases/catalog.py`."""

from app.grpcapi.gen.dop.bff.v1 import catalog_pb2 as bff
from app.grpcapi.gen.dop.bff.v1 import catalog_pb2_grpc as bff_grpc
from app.usecases import catalog as uc


class CatalogServicer(bff_grpc.CatalogServiceServicer):
    async def ListPlans(self, request: bff.ListPlansRequest, context) -> bff.ListPlansResponse:
        return bff.ListPlansResponse(
            plans=[
                bff.Plan(key=p.key, name=p.name, tagline=p.tagline, features=p.features)
                for p in await uc.plans()
            ]
        )

    async def ListProviders(
        self, request: bff.ListProvidersRequest, context
    ) -> bff.ListProvidersResponse:
        return bff.ListProvidersResponse(
            providers=[
                bff.Provider(
                    key=p.key,
                    category=p.category,
                    name=p.name,
                    credential_kind=p.credential_kind,
                    permissions=p.permissions,
                    needs_base_url=p.needs_base_url,
                    operated=p.operated,
                    docs_url=p.docs_url,
                    brand_color=p.brand_color,
                )
                for p in await uc.providers()
            ]
        )
