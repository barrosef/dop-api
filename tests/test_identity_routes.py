"""Rotas de identidade contra um núcleo duplo.

O que se verifica aqui não é o núcleo (ele tem os testes dele), e sim a BORDA:
que o contexto de chamada sai correto no metadado, que a semântica de erro do
core atravessa intacta, e que os decorators barram o que devem barrar.
"""

import grpc
import pytest
from fastapi.testclient import TestClient

from app.coreclient.gen.dop.v1 import common_pb2, identity_pb2
from app.main import create_app
from tests.conftest import token_de

CONTA = {"authorization": token_de(), "x-account-id": "acct-1"}
SEM_CONTA = {"authorization": token_de()}


class TestResolverDeAutorizacao:
    def test_ensure_user_roda_em_todo_login(self, cliente, nucleo):
        """Idempotente por desenho: não é só no primeiro acesso."""
        cliente.get("/api/v1/me", headers=CONTA)
        cliente.get("/api/v1/me", headers=CONTA)
        assert len(nucleo.EnsureUser.chamadas) == 2
        req = nucleo.EnsureUser.ultima["request"]
        assert req.subject == "sub-1"
        assert req.email == "dev@dop.local"
        assert req.provider == "email"  # normalizado, não claim de Firebase
        assert req.idempotency_key

    def test_me_traz_o_user_id_do_core(self, cliente):
        r = cliente.get("/api/v1/me", headers=CONTA)
        assert r.status_code == 200
        corpo = r.json()
        assert corpo["user_id"] == "u-1"  # id do core, não o subject
        assert corpo["subject"] == "sub-1"
        assert corpo["role"] == "admin"
        assert corpo["account_id"] == "acct-1"

    def test_sem_vinculo_com_a_conta_da_403(self, cliente, nucleo):
        """Pedir uma conta de que não se é membro não é 404: é 403."""
        nucleo.ListAccounts.devolve(
            identity_pb2.ListAccountsResponse(
                accounts=[identity_pb2.Account(id="acct-de-outro")]
            )
        )
        r = cliente.get("/api/v1/me", headers=CONTA)
        assert r.status_code == 403

    def test_papel_nao_resolvido_nao_derruba_a_requisicao(self, cliente, nucleo):
        """ListMemberships indisponível degrada para papel vazio, não para 500.

        O vínculo já foi provado por ListAccounts; o que falta é só o papel — e
        quem exige papel recusa depois, com 403.
        """
        nucleo.ListMemberships.falha_com(grpc.StatusCode.UNIMPLEMENTED)
        r = cliente.get("/api/v1/me", headers=CONTA)
        assert r.status_code == 200
        assert r.json()["role"] == ""

        r = cliente.get("/api/v1/accounts/current/members", headers=CONTA)
        assert r.status_code == 403

    def test_nucleo_fora_do_ar_vira_503(self, cliente, nucleo):
        nucleo.EnsureUser.falha_com(grpc.StatusCode.UNAVAILABLE)
        assert cliente.get("/api/v1/me", headers=CONTA).status_code == 503

    def test_sem_conta_ativa_o_login_ainda_funciona(self, cliente, nucleo):
        """É assim que o cockpit carrega o seletor de contas."""
        r = cliente.get("/api/v1/me", headers=SEM_CONTA)
        assert r.status_code == 200
        assert r.json()["user_id"] == "u-1"
        assert not nucleo.ListMemberships.chamadas


class TestPropagacaoDeContexto:
    def test_metadado_leva_a_conta_ativa_e_o_ator(self, cliente, nucleo):
        cliente.get("/api/v1/accounts", headers={**CONTA, "x-request-id": "trace-1"})
        md = nucleo.ListAccounts.metadados()
        assert md["x-account-id"] == "acct-1"
        assert md["x-actor-id"] == "u-1"
        assert md["x-actor-kind"] == "user"
        assert md["x-request-id"] == "trace-1"  # mesmo rastro do BFF ao core

    def test_call_context_vai_no_corpo_da_requisicao(self, cliente, nucleo):
        cliente.get("/api/v1/accounts/current/members", headers=CONTA)
        req = nucleo.ListMemberships.ultima["request"]
        assert req.ctx.account.id == "acct-1"
        assert req.ctx.actor.id == "u-1"
        assert req.account.id == "acct-1"

    def test_chamada_carrega_deadline(self, cliente, nucleo):
        cliente.get("/api/v1/accounts", headers=CONTA)
        assert nucleo.ListAccounts.ultima["timeout"] == 10.0


class TestTraducaoDeErro:
    """O BFF traduz a semântica do core — não inventa a sua.

    Dois caminhos independentes precisam traduzir igual: o handler da aplicação
    (erro dentro da rota) e o as_http (erro dentro do middleware, que roda ACIMA
    do ExceptionMiddleware do Starlette e não seria alcançado pelo handler).
    """

    CASOS = [
        (grpc.StatusCode.NOT_FOUND, 404),
        (grpc.StatusCode.PERMISSION_DENIED, 403),
        (grpc.StatusCode.INVALID_ARGUMENT, 400),
        (grpc.StatusCode.ALREADY_EXISTS, 409),
    ]

    @pytest.mark.parametrize(("code", "http"), CASOS)
    def test_erro_na_rota_vira_status_http(self, cliente, nucleo, code, http):
        nucleo.CreateInvite.falha_com(code)
        r = cliente.post("/api/v1/invites", headers=CONTA, json={"email": "x@dop.local"})
        assert r.status_code == http
        assert r.json()["detail"] == "veio do núcleo"

    @pytest.mark.parametrize(("code", "http"), CASOS)
    def test_erro_no_resolver_vira_status_http(self, cliente, nucleo, code, http):
        nucleo.EnsureUser.falha_com(code)
        assert cliente.get("/api/v1/me", headers=CONTA).status_code == http

    def test_erro_interno_da_rota_nao_vaza_detalhe(self, cliente, nucleo):
        nucleo.CreateInvite.falha_com(grpc.StatusCode.INTERNAL, "senha do banco no log")
        r = cliente.post("/api/v1/invites", headers=CONTA, json={"email": "x@dop.local"})
        assert r.status_code == 500
        assert "senha" not in r.text

    def test_erro_interno_do_resolver_tambem_nao_vaza(self, cliente, nucleo):
        """O middleware traduz na mão — e precisa redigir igual ao handler."""
        nucleo.EnsureUser.falha_com(grpc.StatusCode.INTERNAL, "senha do banco no log")
        r = cliente.get("/api/v1/me", headers=CONTA)
        assert r.status_code == 500
        assert "senha" not in r.text


class TestRotasProtegidas:
    def test_sem_conta_ativa_da_400(self, cliente):
        """Regra do SP-0, cobrada pelo @account_scoped na borda."""
        r = cliente.get("/api/v1/accounts/current/members", headers=SEM_CONTA)
        assert r.status_code == 400

    def test_papel_insuficiente_da_403(self, cliente, nucleo):
        nucleo.ListMemberships.devolve(
            identity_pb2.ListMembershipsResponse(
                memberships=[
                    identity_pb2.Membership(
                        id="m-1",
                        user=common_pb2.UserRef(id="u-1"),
                        role=identity_pb2.ROLE_DEVELOPER,
                    )
                ]
            )
        )
        assert cliente.get("/api/v1/accounts/current/members", headers=CONTA).status_code == 403

    def test_sem_token_da_401(self, cliente):
        assert cliente.get("/api/v1/accounts").status_code == 401


class TestListagens:
    def test_accounts_traduz_o_proto(self, cliente):
        r = cliente.get("/api/v1/accounts", headers=CONTA)
        assert r.status_code == 200
        assert r.json() == [
            {
                "id": "acct-1",
                "handle": "acme",
                "display_name": "ACME",
                "kind": "organization",
                "role": "admin",
            }
        ]

    def test_members_traduz_o_papel_para_string(self, cliente):
        r = cliente.get("/api/v1/accounts/current/members", headers=CONTA)
        assert r.status_code == 200
        assert r.json() == [{"id": "m-1", "user_id": "u-1", "role": "admin"}]


class TestEscritas:
    def test_cria_organizacao_com_chave_de_idempotencia(self, cliente, nucleo):
        r = cliente.post(
            "/api/v1/accounts",
            headers=CONTA,
            json={"handle": "acme", "display_name": "ACME", "legal_id": "00.000.000/0001-00"},
        )
        assert r.status_code == 201
        req = nucleo.CreateAccount.ultima["request"]
        assert req.kind == identity_pb2.Account.KIND_ORGANIZATION
        assert req.handle == "acme"
        assert req.idempotency_key  # repetir não pode duplicar (ADR-0017)

    def test_criar_organizacao_exige_conta_ativa(self, cliente):
        r = cliente.post(
            "/api/v1/accounts", headers=SEM_CONTA, json={"handle": "a", "display_name": "A"}
        )
        assert r.status_code == 400

    def test_cria_convite_com_papel_e_concessoes(self, cliente, nucleo):
        r = cliente.post(
            "/api/v1/invites",
            headers=CONTA,
            json={
                "email": "novo@dop.local",
                "role": "developer",
                "grants": [{"resource_id": "res-1", "level": "use"}],
            },
        )
        assert r.status_code == 201
        assert r.json()["status"] == "pending"
        req = nucleo.CreateInvite.ultima["request"]
        assert req.role == identity_pb2.ROLE_DEVELOPER
        assert req.grants[0].resource.id == "res-1"
        assert req.grants[0].level == "use"
        assert req.ctx.account.id == "acct-1"

    def test_convite_exige_papel_de_gestao(self, monkeypatch, nucleo):
        nucleo.ListMemberships.devolve(
            identity_pb2.ListMembershipsResponse(
                memberships=[
                    identity_pb2.Membership(id="m-1", role=identity_pb2.ROLE_DEVELOPER)
                ]
            )
        )
        with TestClient(create_app()) as c:
            r = c.post("/api/v1/invites", headers=CONTA, json={"email": "x@dop.local"})
        assert r.status_code == 403
        assert not nucleo.CreateInvite.chamadas
