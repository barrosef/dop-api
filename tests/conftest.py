"""Duplos do núcleo. Nenhum teste sobe o dop-core de verdade.

O ponto de substituição é UM só: `app.coreclient.stubs.identity_stub`. Router e
resolver passam por ele, então trocar essa função troca o núcleo inteiro — sem
patch espalhado por módulo.
"""

import base64
import json
import time

import grpc
import pytest
from fastapi.testclient import TestClient
from grpc.aio import AioRpcError

from app.coreclient import stubs
from app.coreclient.gen.dop.v1 import common_pb2, identity_pb2
from app.main import create_app

PROJECT = "dop-local"


def token_de(subject="sub-1", email="dev@dop.local", name="Dev"):
    """Token do emulador: não é assinado, mas normaliza igual ao de produção."""
    payload = {
        "sub": subject,
        "aud": PROJECT,
        "exp": time.time() + 3600,
        "email": email,
        "email_verified": True,
        "name": name,
        "firebase": {"sign_in_provider": "password", "identities": {"email": [email]}},
    }
    raw = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
    return f"Bearer cabecalho.{raw}.assinatura"


def erro_grpc(code: grpc.StatusCode, details="veio do núcleo") -> AioRpcError:
    return AioRpcError(code, grpc.aio.Metadata(), grpc.aio.Metadata(), details)


class ChamadaFalsa:
    """Um RPC. Guarda o que recebeu; devolve o que mandaram devolver."""

    def __init__(self, resultado=None):
        self.resultado = resultado
        self.chamadas: list[dict] = []

    def devolve(self, resultado):
        self.resultado = resultado
        return self

    def falha_com(self, code: grpc.StatusCode, details="veio do núcleo"):
        self.resultado = erro_grpc(code, details)
        return self

    @property
    def ultima(self) -> dict:
        assert self.chamadas, "o RPC não foi chamado"
        return self.chamadas[-1]

    def metadados(self) -> dict[str, str]:
        return dict(self.ultima["metadata"])

    async def __call__(self, request, *, metadata=None, timeout=None, **_):
        self.chamadas.append({"request": request, "metadata": metadata, "timeout": timeout})
        if isinstance(self.resultado, Exception):
            raise self.resultado
        return self.resultado


class NucleoFalso:
    """Stub do IdentityService com os RPCs que a borda usa."""

    def __init__(self, user_id="u-1", account_id="acct-1", role=identity_pb2.ROLE_ADMIN):
        self.user_id = user_id
        conta = identity_pb2.Account(
            id=account_id,
            kind=identity_pb2.Account.KIND_ORGANIZATION,
            handle="acme",
            display_name="ACME",
        )
        self.EnsureUser = ChamadaFalsa(identity_pb2.User(id=user_id, email="dev@dop.local"))
        self.ListAccounts = ChamadaFalsa(
            identity_pb2.ListAccountsResponse(accounts=[conta])
        )
        self.ListMemberships = ChamadaFalsa(
            identity_pb2.ListMembershipsResponse(
                memberships=[
                    identity_pb2.Membership(
                        id="m-1",
                        user=common_pb2.UserRef(id=user_id),
                        account=common_pb2.AccountRef(id=account_id),
                        role=role,
                    )
                ]
            )
        )
        self.CreateAccount = ChamadaFalsa(conta)
        self.CreateInvite = ChamadaFalsa(
            identity_pb2.Invite(
                id="inv-1",
                email="novo@dop.local",
                role=identity_pb2.ROLE_DEVELOPER,
                status=identity_pb2.Invite.STATUS_PENDING,
            )
        )


@pytest.fixture
def nucleo(monkeypatch):
    monkeypatch.setenv("FIREBASE_AUTH_EMULATOR_HOST", "localhost:9099")
    falso = NucleoFalso()
    monkeypatch.setattr(stubs, "identity_stub", lambda: falso)
    return falso


@pytest.fixture
def cliente(nucleo):
    with TestClient(create_app()) as c:
        yield c
