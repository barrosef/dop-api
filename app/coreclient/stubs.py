"""Fábricas de stub — um ponto único de criação.

Existir como função (e não como stub global) tem duas razões: o canal só fica
pronto depois do lifespan, e o teste substitui UMA função em vez de caçar
importações espalhadas.

Uma fábrica por serviço do núcleo, todas iguais de propósito: quem escreve um
caso de uso novo não precisa decidir nada aqui, e quem escreve teste sabe
exatamente qual nome trocar.
"""

from app.coreclient.client import core
from app.coreclient.gen.dop.v1 import (
    agent_pb2_grpc,
    attention_pb2_grpc,
    cost_pb2_grpc,
    delivery_pb2_grpc,
    demand_pb2_grpc,
    event_pb2_grpc,
    execution_pb2_grpc,
    hierarchy_pb2_grpc,
    identity_pb2_grpc,
    knowledge_pb2_grpc,
    resource_pb2_grpc,
    workflow_pb2_grpc,
)


def identity_stub() -> identity_pb2_grpc.IdentityServiceStub:
    return identity_pb2_grpc.IdentityServiceStub(core.channel)


def resource_stub() -> resource_pb2_grpc.ResourceServiceStub:
    return resource_pb2_grpc.ResourceServiceStub(core.channel)


def hierarchy_stub() -> hierarchy_pb2_grpc.HierarchyServiceStub:
    return hierarchy_pb2_grpc.HierarchyServiceStub(core.channel)


def workflow_stub() -> workflow_pb2_grpc.WorkflowServiceStub:
    return workflow_pb2_grpc.WorkflowServiceStub(core.channel)


def demand_stub() -> demand_pb2_grpc.DemandServiceStub:
    return demand_pb2_grpc.DemandServiceStub(core.channel)


def knowledge_stub() -> knowledge_pb2_grpc.KnowledgeServiceStub:
    return knowledge_pb2_grpc.KnowledgeServiceStub(core.channel)


def cost_stub() -> cost_pb2_grpc.CostServiceStub:
    return cost_pb2_grpc.CostServiceStub(core.channel)


def delivery_stub() -> delivery_pb2_grpc.DeliveryServiceStub:
    return delivery_pb2_grpc.DeliveryServiceStub(core.channel)


def execution_stub() -> execution_pb2_grpc.ExecutionServiceStub:
    return execution_pb2_grpc.ExecutionServiceStub(core.channel)


def event_stub() -> event_pb2_grpc.EventServiceStub:
    """Streaming de eventos ao vivo — a origem do SSE que o cockpit consome."""
    return event_pb2_grpc.EventServiceStub(core.channel)


def attention_stub() -> attention_pb2_grpc.AttentionServiceStub:
    """A caixa de atenção: lista e stream da fila única de pendências."""
    return attention_pb2_grpc.AttentionServiceStub(core.channel)


def agent_stub() -> agent_pb2_grpc.AgentServiceStub:
    """Runtime de agente — vive no NÚCLEO (ADR-0023).

    O BFF não executa turno e não vê credencial de provedor: ele é a camada
    exposta à internet, e comprometê-la não pode entregar as credenciais de
    agente de todas as contas.
    """
    return agent_pb2_grpc.AgentServiceStub(core.channel)
