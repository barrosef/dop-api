"""Fábricas de stub — um ponto único de criação.

Existir como função (e não como stub global) tem duas razões: o canal só fica
pronto depois do lifespan, e o teste substitui UMA função em vez de caçar
importações espalhadas.
"""

from app.coreclient.client import core
from app.coreclient.gen.dop.v1 import (
    hierarchy_pb2_grpc,
    identity_pb2_grpc,
    resource_pb2_grpc,
)


def identity_stub() -> identity_pb2_grpc.IdentityServiceStub:
    return identity_pb2_grpc.IdentityServiceStub(core.channel)


def resource_stub() -> resource_pb2_grpc.ResourceServiceStub:
    return resource_pb2_grpc.ResourceServiceStub(core.channel)


def hierarchy_stub() -> hierarchy_pb2_grpc.HierarchyServiceStub:
    return hierarchy_pb2_grpc.HierarchyServiceStub(core.channel)
