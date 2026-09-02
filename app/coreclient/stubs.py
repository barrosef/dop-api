"""Stub factories — a single point of creation.

Existing as functions (and not as global stubs) has two reasons: the channel is
only ready after the lifespan, and a test replaces ONE function instead of
hunting scattered imports.

One factory per core service, all alike on purpose: whoever writes a new use
case does not have to decide anything here, and whoever writes a test knows
exactly which name to swap.
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
    secondfactor_pb2_grpc,
    workflow_pb2_grpc,
)


def identity_stub() -> identity_pb2_grpc.IdentityServiceStub:
    return identity_pb2_grpc.IdentityServiceStub(core.channel)


def resource_stub() -> resource_pb2_grpc.ResourceServiceStub:
    return resource_pb2_grpc.ResourceServiceStub(core.channel)


def second_factor_stub() -> secondfactor_pb2_grpc.SecondFactorServiceStub:
    return secondfactor_pb2_grpc.SecondFactorServiceStub(core.channel)


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
    """Live event streaming — the source of the SSE the cockpit consumes."""
    return event_pb2_grpc.EventServiceStub(core.channel)


def attention_stub() -> attention_pb2_grpc.AttentionServiceStub:
    """The attention box: the list and the stream of the single queue of pending items."""
    return attention_pb2_grpc.AttentionServiceStub(core.channel)


def agent_stub() -> agent_pb2_grpc.AgentServiceStub:
    """The agent runtime — it lives in the CORE (ADR-0023).

    The BFF does not run a turn and does not see a provider credential: it is
    the layer exposed to the internet, and compromising it must not hand over
    every account's agent credentials.
    """
    return agent_pb2_grpc.AgentServiceStub(core.channel)
