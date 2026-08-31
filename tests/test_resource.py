"""Recursos nos dois transportes.

O teste mais importante deste arquivo é o de vazamento: nenhuma resposta pode
conter o segredo. Ele é escrito procurando o VALOR no corpo serializado inteiro,
e não conferindo campo por campo — conferir campo por campo só pega o vazamento
que alguém lembrou de imaginar.
"""

import base64

import grpc
import pytest

from app.grpcapi.gen.dop.bff.v1 import resource_pb2 as bff
from tests.conftest import metadados_de, token_de

CONTA = metadados_de(token_de())
CABECALHOS_REST = {"authorization": token_de(), "x-account-id": "acct-1"}

SEGREDO = "pk_live_nao_pode_vazar_jamais"
SEGREDO_B64 = base64.b64encode(SEGREDO.encode()).decode()


class TestSegredoNaoVaza:
    def test_rest_nao_devolve_o_valor(self, cliente_res):
        r = cliente_res.put(
            "/api/v1/resources/res-1/credential",
            headers=CABECALHOS_REST,
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
            metadata=CONTA,
        )
        assert SEGREDO.encode() not in resp.SerializeToString()
        assert resp.credential_ref.startswith("integration_credential:")

    def test_o_segredo_chegou_ao_nucleo(self, cliente_res, recursos):
        """Não vazar não pode virar não gravar."""
        cliente_res.put(
            "/api/v1/resources/res-1/credential",
            headers=CABECALHOS_REST,
            json={"secret_base64": SEGREDO_B64},
        )
        assert recursos.SetCredential.pedidos[0].secret == SEGREDO.encode()

    def test_listar_nao_traz_valor(self, cliente_res):
        r = cliente_res.get("/api/v1/resources", headers=CABECALHOS_REST)
        assert SEGREDO not in r.text


class TestREST:
    def test_listar_integracoes(self, cliente_res):
        r = cliente_res.get("/api/v1/resources?kind=integration", headers=CABECALHOS_REST)
        assert r.status_code == 200
        assert r.json()[0]["kind"] == "integration"
        assert r.json()[0]["config"]["provider"] == "clickup"

    def test_criar_carrega_idempotencia(self, cliente_res, recursos):
        cliente_res.post(
            "/api/v1/resources",
            headers=CABECALHOS_REST,
            json={"kind": "integration", "name": "GitHub", "config": {"category": "git"}},
        )
        assert recursos.CreateResource.pedidos[0].idempotency_key != ""

    def test_nivel_de_concessao_e_validado_na_borda(self, cliente_res):
        """`level` só aceita use ou manage — 422 antes de gastar ida ao núcleo."""
        r = cliente_res.post(
            "/api/v1/grants",
            headers=CABECALHOS_REST,
            json={"resource_id": "res-1", "user_id": "u-2", "level": "dono"},
        )
        assert r.status_code == 422

    def test_conceder_exige_papel(self, cliente_res, nucleo):
        nucleo.papel_developer()
        r = cliente_res.post(
            "/api/v1/grants",
            headers=CABECALHOS_REST,
            json={"resource_id": "res-1", "user_id": "u-2", "level": "use"},
        )
        assert r.status_code == 403


class TestGRPC:
    async def test_listar(self, stub_res):
        resp = await stub_res.ListResources(
            bff.ListResourcesRequest(kind=bff.RESOURCE_KIND_INTEGRATION), metadata=CONTA
        )
        assert resp.resources[0].kind == bff.RESOURCE_KIND_INTEGRATION

    async def test_sem_token(self, stub_res):
        with pytest.raises(grpc.aio.AioRpcError) as e:
            await stub_res.ListResources(
                bff.ListResourcesRequest(), metadata=metadados_de(None)
            )
        assert e.value.code() == grpc.StatusCode.UNAUTHENTICATED

    async def test_conceder_exige_papel(self, stub_res, nucleo):
        nucleo.papel_developer()
        with pytest.raises(grpc.aio.AioRpcError) as e:
            await stub_res.GrantResource(
                bff.GrantResourceRequest(resource_id="res-1", user_id="u-2", level="use"),
                metadata=CONTA,
            )
        assert e.value.code() == grpc.StatusCode.PERMISSION_DENIED


class TestParidadeEntreTransportes:
    async def test_listar_igual_nas_duas_portas(self, cliente_res, stub_res):
        rest = cliente_res.get("/api/v1/resources", headers=CABECALHOS_REST).json()
        resp = await stub_res.ListResources(bff.ListResourcesRequest(), metadata=CONTA)
        assert len(resp.resources) == len(rest)
        for g, j in zip(resp.resources, rest, strict=True):
            assert g.id == j["id"]
            assert g.name == j["name"]
            assert g.credential_ref == j["credential_ref"]
            assert dict(g.config) == j["config"]
