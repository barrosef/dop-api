"""The BFF's gRPC server — it comes up in the SAME process as FastAPI, on its own port.

Why in the same process: the two ports serve the same use cases, with the same
channel to the core and the same token verifier. Splitting them into two
processes would duplicate configuration, connection and deployment for no gain —
what separates REST from gRPC here is the adapter, not the runtime.

Asynchronous (`grpc.aio`) because the rest of the BFF is: a synchronous server
would need a thread pool in order to call an `async` use case, and each thread
would have its own ContextVar — the decorators would read an empty context.

Its life cycle is in FastAPI's lifespan: it comes up after the channel to the
core and goes down before it, with a graceful shutdown.
"""

import grpc
from grpc_reflection.v1alpha import reflection

from app.grpcapi.attention import AttentionServicer
from app.grpcapi.cost import CostServicer
from app.grpcapi.delivery import DeliveryServicer
from app.grpcapi.demand import DemandServicer
from app.grpcapi.execution import ExecutionServicer
from app.grpcapi.gen.dop.bff.v1 import attention_pb2 as bff_attention_pb2
from app.grpcapi.gen.dop.bff.v1 import attention_pb2_grpc as bff_attention_grpc
from app.grpcapi.gen.dop.bff.v1 import cost_pb2 as bff_cost_pb2
from app.grpcapi.gen.dop.bff.v1 import cost_pb2_grpc as bff_cost_grpc
from app.grpcapi.gen.dop.bff.v1 import delivery_pb2 as bff_delivery_pb2
from app.grpcapi.gen.dop.bff.v1 import delivery_pb2_grpc as bff_delivery_grpc
from app.grpcapi.gen.dop.bff.v1 import demand_pb2 as bff_demand_pb2
from app.grpcapi.gen.dop.bff.v1 import demand_pb2_grpc as bff_demand_grpc
from app.grpcapi.gen.dop.bff.v1 import execution_pb2 as bff_execution_pb2
from app.grpcapi.gen.dop.bff.v1 import execution_pb2_grpc as bff_execution_grpc
from app.grpcapi.gen.dop.bff.v1 import hierarchy_pb2 as bff_hier_pb2
from app.grpcapi.gen.dop.bff.v1 import hierarchy_pb2_grpc as bff_hier_grpc
from app.grpcapi.gen.dop.bff.v1 import identity_pb2 as bff_pb2
from app.grpcapi.gen.dop.bff.v1 import identity_pb2_grpc as bff_grpc
from app.grpcapi.gen.dop.bff.v1 import knowledge_pb2 as bff_knowledge_pb2
from app.grpcapi.gen.dop.bff.v1 import knowledge_pb2_grpc as bff_knowledge_grpc
from app.grpcapi.gen.dop.bff.v1 import resource_pb2 as bff_res_pb2
from app.grpcapi.gen.dop.bff.v1 import resource_pb2_grpc as bff_res_grpc
from app.grpcapi.gen.dop.bff.v1 import runtime_pb2 as bff_runtime_pb2
from app.grpcapi.gen.dop.bff.v1 import runtime_pb2_grpc as bff_runtime_grpc
from app.grpcapi.gen.dop.bff.v1 import secondfactor_pb2_grpc as bff_2fa_grpc
from app.grpcapi.gen.dop.bff.v1 import stream_pb2 as bff_stream_pb2
from app.grpcapi.gen.dop.bff.v1 import stream_pb2_grpc as bff_stream_grpc
from app.grpcapi.gen.dop.bff.v1 import workflow_pb2 as bff_workflow_pb2
from app.grpcapi.gen.dop.bff.v1 import workflow_pb2_grpc as bff_workflow_grpc
from app.grpcapi.hierarchy import HierarchyServicer
from app.grpcapi.identity import IdentityServicer
from app.grpcapi.interceptors import AuthInterceptor, ErrorInterceptor, LoggingInterceptor
from app.grpcapi.knowledge import KnowledgeServicer
from app.grpcapi.resource import ResourceServicer
from app.grpcapi.runtime import RuntimeServicer
from app.grpcapi.secondfactor import SecondFactorServicer
from app.grpcapi.stream import StreamServicer
from app.grpcapi.workflow import WorkflowServicer
from app.platform.logging.config import get_logger
from app.platform.security.firebase import FirebaseVerifier

# The seconds the shutdown waits for in-flight calls to finish. Dropping in the
# middle of a write would leave the client not knowing whether the effect
# happened.
GRACE_S = 5.0


class GrpcServer:
    """The edge's gRPC server, with its life cycle tied to FastAPI's."""

    def __init__(
        self,
        *,
        verifier: FirebaseVerifier,
        resolver=None,
        port: int = 0,
        host: str = "[::]",
    ):
        self._verifier = verifier
        self._resolver = resolver
        self._host = host
        # port 0 = the system chooses. It is what the tests use so they can
        # bring several servers up without colliding; in production it comes from
        # the configuration.
        self._requested_port = port
        self._server: grpc.aio.Server | None = None
        self.port = 0

    async def start(self) -> None:
        self._server = grpc.aio.server(
            interceptors=(
                # The same order as the HTTP stack: logging outermost (it
                # measures everything, the authentication refusal included),
                # error translation in the middle, auth innermost (next to the
                # servicer, as AuthMiddleware is to the router).
                LoggingInterceptor(),
                ErrorInterceptor(),
                AuthInterceptor(self._verifier, self._resolver),
            )
        )
        bff_grpc.add_IdentityServiceServicer_to_server(IdentityServicer(), self._server)
        bff_hier_grpc.add_HierarchyServiceServicer_to_server(HierarchyServicer(), self._server)
        bff_res_grpc.add_ResourceServiceServicer_to_server(ResourceServicer(), self._server)
        bff_2fa_grpc.add_SecondFactorServiceServicer_to_server(SecondFactorServicer(), self._server)
        bff_runtime_grpc.add_RuntimeServiceServicer_to_server(RuntimeServicer(), self._server)
        bff_attention_grpc.add_AttentionServiceServicer_to_server(AttentionServicer(), self._server)
        bff_cost_grpc.add_CostServiceServicer_to_server(CostServicer(), self._server)
        bff_delivery_grpc.add_DeliveryServiceServicer_to_server(DeliveryServicer(), self._server)
        bff_demand_grpc.add_DemandServiceServicer_to_server(DemandServicer(), self._server)
        bff_execution_grpc.add_ExecutionServiceServicer_to_server(ExecutionServicer(), self._server)
        bff_knowledge_grpc.add_KnowledgeServiceServicer_to_server(KnowledgeServicer(), self._server)
        bff_stream_grpc.add_StreamServiceServicer_to_server(StreamServicer(), self._server)
        bff_workflow_grpc.add_WorkflowServiceServicer_to_server(WorkflowServicer(), self._server)

        # Reflection, as in the core (internal/app/run.go): without it, grpcurl
        # and Bruno cannot even list the surface, and the edge ends up less
        # explorable than the internal service it wraps.
        reflection.enable_server_reflection(
            (
                bff_pb2.DESCRIPTOR.services_by_name["IdentityService"].full_name,
                bff_hier_pb2.DESCRIPTOR.services_by_name["HierarchyService"].full_name,
                bff_res_pb2.DESCRIPTOR.services_by_name["ResourceService"].full_name,
                bff_runtime_pb2.DESCRIPTOR.services_by_name["RuntimeService"].full_name,
                bff_attention_pb2.DESCRIPTOR.services_by_name["AttentionService"].full_name,
                bff_cost_pb2.DESCRIPTOR.services_by_name["CostService"].full_name,
                bff_delivery_pb2.DESCRIPTOR.services_by_name["DeliveryService"].full_name,
                bff_demand_pb2.DESCRIPTOR.services_by_name["DemandService"].full_name,
                bff_execution_pb2.DESCRIPTOR.services_by_name["ExecutionService"].full_name,
                bff_knowledge_pb2.DESCRIPTOR.services_by_name["KnowledgeService"].full_name,
                bff_stream_pb2.DESCRIPTOR.services_by_name["StreamService"].full_name,
                bff_workflow_pb2.DESCRIPTOR.services_by_name["WorkflowService"].full_name,
                reflection.SERVICE_NAME,
            ),
            self._server,
        )

        self.port = self._server.add_insecure_port(f"{self._host}:{self._requested_port}")
        if self.port == 0:
            raise RuntimeError(f"could not listen on {self._host}:{self._requested_port}")

        await self._server.start()
        get_logger().info("gRPC port opened", grpc_port=self.port)

    async def stop(self, grace: float = GRACE_S) -> None:
        if self._server is not None:
            await self._server.stop(grace)
            self._server = None
            get_logger().info("gRPC port closed")
