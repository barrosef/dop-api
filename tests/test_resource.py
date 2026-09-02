"""Recursos nos dois transportes.

O teste mais importante deste arquivo é o de vazamento: nenhuma response pode
conter o segredo. Ele é escrito procurando o VALOR no body serializado inteiro,
e não conferindo campo por campo — conferir campo por campo só pega o vazamento
que alguém lembrou de imaginar.
"""

import base64

import grpc
import pytest

from app.grpcapi.gen.dop.bff.v1 import resource_pb2 as bff
from tests.conftest import metadata_for, token_for

ACCOUNT = metadata_for(token_for())
REST_HEADERS = {"authorization": token_for(), "x-account-id": "acct-1"}

SEGREDO = "pk_live_nao_pode_vazar_jamais"
SEGREDO_B64 = base64.b64encode(SEGREDO.encode()).decode()


class TestSegredoNaoVaza:
    def test_rest_nao_devolve_o_valor(self, client_res):
        r = client_res.put(
            "/api/v1/resources/res-1/credential",
            headers=REST_HEADERS,
            json={"secret_base64": SEGREDO_B64},
        )
        assert r.status_code == 200
        assert SEGREDO not in r.text
        assert SEGREDO_B64 not in r.text
        # O que volta é o rótulo opaco, que diz que HÁ credencial.
        assert r.json()["credential_ref"].startswith("integration_credential:")

    async def test_grpc_nao_devolve_o_valor(self, stub_res):
        resp = await stub_res.SetCredential(
            bff.SetCredentialRequest(resource_id="res-1", secret=SEGREDO.encode()),
            metadata=ACCOUNT,
        )
        assert SEGREDO.encode() not in resp.SerializeToString()
        assert resp.credential_ref.startswith("integration_credential:")

    def test_o_segredo_chegou_ao_nucleo(self, client_res, resources):
        """Não vazar não pode virar não gravar."""
        client_res.put(
            "/api/v1/resources/res-1/credential",
            headers=REST_HEADERS,
            json={"secret_base64": SEGREDO_B64},
        )
        assert resources.SetCredential.requests[0].secret == SEGREDO.encode()

    def test_listar_nao_traz_valor(self, client_res):
        r = client_res.get("/api/v1/resources", headers=REST_HEADERS)
        assert SEGREDO not in r.text


class TestREST:
    def test_listar_integracoes(self, client_res):
        r = client_res.get("/api/v1/resources?kind=integration", headers=REST_HEADERS)
        assert r.status_code == 200
        assert r.json()[0]["kind"] == "integration"
        assert r.json()[0]["config"]["provider"] == "clickup"

    def test_criar_carrega_idempotencia(self, client_res, resources):
        client_res.post(
            "/api/v1/resources",
            headers=REST_HEADERS,
            json={"kind": "integration", "name": "GitHub", "config": {"category": "git"}},
        )
        assert resources.CreateResource.requests[0].idempotency_key != ""

    def test_nivel_de_concessao_e_validado_na_borda(self, client_res):
        """`level` só aceita use ou manage — 422 antes de gastar ida ao núcleo."""
        r = client_res.post(
            "/api/v1/grants",
            headers=REST_HEADERS,
            json={"resource_id": "res-1", "user_id": "u-2", "level": "dono"},
        )
        assert r.status_code == 422

    def test_conceder_exige_papel(self, client_res, core):
        core.demote_to_developer()
        r = client_res.post(
            "/api/v1/grants",
            headers=REST_HEADERS,
            json={"resource_id": "res-1", "user_id": "u-2", "level": "use"},
        )
        assert r.status_code == 403


class TestGRPC:
    async def test_listar(self, stub_res):
        resp = await stub_res.ListResources(
            bff.ListResourcesRequest(kind=bff.RESOURCE_KIND_INTEGRATION), metadata=ACCOUNT
        )
        assert resp.resources[0].kind == bff.RESOURCE_KIND_INTEGRATION

    async def test_sem_token(self, stub_res):
        with pytest.raises(grpc.aio.AioRpcError) as e:
            await stub_res.ListResources(
                bff.ListResourcesRequest(), metadata=metadata_for(None)
            )
        assert e.value.code() == grpc.StatusCode.UNAUTHENTICATED

    async def test_conceder_exige_papel(self, stub_res, core):
        core.demote_to_developer()
        with pytest.raises(grpc.aio.AioRpcError) as e:
            await stub_res.GrantResource(
                bff.GrantResourceRequest(resource_id="res-1", user_id="u-2", level="use"),
                metadata=ACCOUNT,
            )
        assert e.value.code() == grpc.StatusCode.PERMISSION_DENIED


class TestParidadeEntreTransportes:
    async def test_listar_igual_nas_duas_portas(self, client_res, stub_res):
        rest = client_res.get("/api/v1/resources", headers=REST_HEADERS).json()
        resp = await stub_res.ListResources(bff.ListResourcesRequest(), metadata=ACCOUNT)
        assert len(resp.resources) == len(rest)
        for g, j in zip(resp.resources, rest, strict=True):
            assert g.id == j["id"]
            assert g.name == j["name"]
            assert g.credential_ref == j["credential_ref"]
            assert dict(g.config) == j["config"]
