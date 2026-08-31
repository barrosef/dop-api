"""Servidor gRPC do BFF — sobe no MESMO processo do FastAPI, em porta própria.

Por que no mesmo processo: as duas portas servem os mesmos casos de uso, com o
mesmo canal para o núcleo e o mesmo verificador de token. Separar em dois
processos duplicaria configuração, conexão e deploy para não ganhar nada — o
que separa REST de gRPC aqui é o adaptador, não o runtime.

Assíncrono (`grpc.aio`) porque o resto do BFF é: um servidor síncrono precisaria
de um pool de threads para poder chamar um caso de uso `async`, e cada thread
teria o seu próprio ContextVar — os decorators leriam contexto vazio.

Ciclo de vida no lifespan do FastAPI: sobe depois do canal com o núcleo, desce
antes dele, com encerramento gracioso.
"""

import grpc
from grpc_reflection.v1alpha import reflection

from app.grpcapi.cost import CostServicer
from app.grpcapi.delivery import DeliveryServicer
from app.grpcapi.demand import DemandServicer
from app.grpcapi.execution import ExecutionServicer
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
from app.grpcapi.gen.dop.bff.v1 import stream_pb2 as bff_stream_pb2
from app.grpcapi.gen.dop.bff.v1 import stream_pb2_grpc as bff_stream_grpc
from app.grpcapi.gen.dop.bff.v1 import workflow_pb2 as bff_workflow_pb2
from app.grpcapi.gen.dop.bff.v1 import workflow_pb2_grpc as bff_workflow_grpc
from app.grpcapi.hierarchy import HierarchyServicer
from app.grpcapi.identity import IdentityServicer
from app.grpcapi.interceptors import AuthInterceptor, ErrorInterceptor, LoggingInterceptor
from app.grpcapi.knowledge import KnowledgeServicer
from app.grpcapi.resource import ResourceServicer
from app.grpcapi.stream import StreamServicer
from app.grpcapi.workflow import WorkflowServicer
from app.platform.logging.config import get_logger
from app.platform.security.firebase import FirebaseVerifier

# Segundos que o encerramento espera as chamadas em voo terminarem. Derrubar no
# meio de uma escrita deixaria o cliente sem saber se o efeito aconteceu.
GRACE_S = 5.0


class GrpcServer:
    """Servidor gRPC da borda, com o ciclo de vida amarrado ao do FastAPI."""

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
        # porta 0 = o sistema escolhe. É o que o teste usa para poder subir
        # vários servidores sem colidir; em produção vem da configuração.
        self._requested_port = port
        self._server: grpc.aio.Server | None = None
        self.port = 0

    async def start(self) -> None:
        self._server = grpc.aio.server(
            interceptors=(
                # Mesma ordem da pilha HTTP: log por fora (mede tudo, inclusive
                # a recusa de autenticação), tradução de erro no meio, auth por
                # dentro (perto do servicer, como o AuthMiddleware é do router).
                LoggingInterceptor(),
                ErrorInterceptor(),
                AuthInterceptor(self._verifier, self._resolver),
            )
        )
        bff_grpc.add_IdentityServiceServicer_to_server(IdentityServicer(), self._server)
        bff_hier_grpc.add_HierarchyServiceServicer_to_server(HierarchyServicer(), self._server)
        bff_res_grpc.add_ResourceServiceServicer_to_server(ResourceServicer(), self._server)
        bff_cost_grpc.add_CostServiceServicer_to_server(CostServicer(), self._server)
        bff_delivery_grpc.add_DeliveryServiceServicer_to_server(DeliveryServicer(), self._server)
        bff_demand_grpc.add_DemandServiceServicer_to_server(DemandServicer(), self._server)
        bff_execution_grpc.add_ExecutionServiceServicer_to_server(ExecutionServicer(), self._server)
        bff_knowledge_grpc.add_KnowledgeServiceServicer_to_server(KnowledgeServicer(), self._server)
        bff_stream_grpc.add_StreamServiceServicer_to_server(StreamServicer(), self._server)
        bff_workflow_grpc.add_WorkflowServiceServicer_to_server(WorkflowServicer(), self._server)

        # Reflection, como no núcleo (internal/app/run.go): sem ela, grpcurl e
        # Bruno não conseguem sequer listar a superfície, e a borda fica menos
        # explorável que o serviço interno que ela embrulha.
        reflection.enable_server_reflection(
            (
                bff_pb2.DESCRIPTOR.services_by_name["IdentityService"].full_name,
                bff_hier_pb2.DESCRIPTOR.services_by_name["HierarchyService"].full_name,
                bff_res_pb2.DESCRIPTOR.services_by_name["ResourceService"].full_name,
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
            raise RuntimeError(f"não foi possível ouvir em {self._host}:{self._requested_port}")

        await self._server.start()
        get_logger().info("porta gRPC aberta", grpc_port=self.port)

    async def stop(self, grace: float = GRACE_S) -> None:
        if self._server is not None:
            await self._server.stop(grace)
            self._server = None
            get_logger().info("porta gRPC encerrada")
