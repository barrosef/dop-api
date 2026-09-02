"""Rotas de identidade contra um núcleo double.

O que se verifica aqui não é o núcleo (ele tem os testes dele), e sim a BORDA:
que o context de call sai correto no metadado, que a semântica de err do
core atravessa intacta, e que os decorators barram o que devem barrar.
"""

import grpc
import pytest
from fastapi.testclient import TestClient

from app.coreclient.gen.dop.v1 import common_pb2, identity_pb2
from app.main import create_app
from tests.conftest import token_for

ACCOUNT = {"authorization": token_for(), "x-account-id": "acct-1"}
SEM_CONTA = {"authorization": token_for()}


class TestResolverDeAutorizacao:
    def test_ensure_user_roda_em_todo_login(self, client, core):
        """Idempotente por desenho: não é só no primeiro acesso."""
        client.get("/api/v1/me", headers=ACCOUNT)
        client.get("/api/v1/me", headers=ACCOUNT)
        assert len(core.EnsureUser.calls) == 2
        req = core.EnsureUser.last["request"]
        assert req.subject == "sub-1"
        assert req.email == "dev@dop.local"
        assert req.provider == "email"  # normalizado, não claim de Firebase
        assert req.idempotency_key

    def test_me_traz_o_user_id_do_core(self, client):
        r = client.get("/api/v1/me", headers=ACCOUNT)
        assert r.status_code == 200
        body = r.json()
        assert body["user_id"] == "u-1"  # id do core, não o subject
        assert body["subject"] == "sub-1"
        assert body["role"] == "admin"
        assert body["account_id"] == "acct-1"

    def test_sem_vinculo_com_a_conta_da_403(self, client, core):
        """Pedir uma account de que não se é membro não é 404: é 403."""
        core.ListAccounts.returns(
            identity_pb2.ListAccountsResponse(
                accounts=[identity_pb2.Account(id="acct-de-outro")]
            )
        )
        r = client.get("/api/v1/me", headers=ACCOUNT)
        assert r.status_code == 403

    def test_papel_nao_resolvido_nao_derruba_a_requisicao(self, client, core):
        """ListMemberships indisponível degrada para role vazio, não para 500.

        O vínculo já foi provado por ListAccounts; o que falta é só o role — e
        quem exige role recusa depois, com 403.
        """
        core.ListMemberships.fails_with(grpc.StatusCode.UNIMPLEMENTED)
        r = client.get("/api/v1/me", headers=ACCOUNT)
        assert r.status_code == 200
        assert r.json()["role"] == ""

        r = client.get("/api/v1/accounts/current/members", headers=ACCOUNT)
        assert r.status_code == 403

    def test_nucleo_fora_do_ar_vira_503(self, client, core):
        core.EnsureUser.fails_with(grpc.StatusCode.UNAVAILABLE)
        assert client.get("/api/v1/me", headers=ACCOUNT).status_code == 503

    def test_sem_conta_ativa_o_login_ainda_funciona(self, client, core):
        """É assim que o cockpit carrega o seletor de contas."""
        r = client.get("/api/v1/me", headers=SEM_CONTA)
        assert r.status_code == 200
        assert r.json()["user_id"] == "u-1"
        assert not core.ListMemberships.calls


class TestPropagacaoDeContexto:
    def test_metadado_leva_a_conta_ativa_e_o_ator(self, client, core):
        client.get("/api/v1/accounts", headers={**ACCOUNT, "x-request-id": "trace-1"})
        md = core.ListAccounts.metadata()
        assert md["x-account-id"] == "acct-1"
        assert md["x-actor-id"] == "u-1"
        assert md["x-actor-kind"] == "user"
        assert md["x-request-id"] == "trace-1"  # mesmo rastro do BFF ao core

    def test_call_context_vai_no_corpo_da_requisicao(self, client, core):
        client.get("/api/v1/accounts/current/members", headers=ACCOUNT)
        req = core.ListMemberships.last["request"]
        assert req.ctx.account.id == "acct-1"
        assert req.ctx.actor.id == "u-1"
        assert req.account.id == "acct-1"

    def test_chamada_carrega_deadline(self, client, core):
        client.get("/api/v1/accounts", headers=ACCOUNT)
        assert core.ListAccounts.last["timeout"] == 10.0


class TestTraducaoDeErro:
    """O BFF traduz a semântica do core — não inventa a sua.

    Dois caminhos independentes precisam traduzir igual: o handler da aplicação
    (err dentro da rota) e o as_http (err dentro do middleware, que roda ACIMA
    do ExceptionMiddleware do Starlette e não seria alcançado pelo handler).
    """

    CASOS = [
        (grpc.StatusCode.NOT_FOUND, 404),
        (grpc.StatusCode.PERMISSION_DENIED, 403),
        (grpc.StatusCode.INVALID_ARGUMENT, 400),
        (grpc.StatusCode.ALREADY_EXISTS, 409),
    ]

    @pytest.mark.parametrize(("code", "http"), CASOS)
    def test_erro_na_rota_vira_status_http(self, client, core, code, http):
        core.CreateInvite.fails_with(code)
        r = client.post("/api/v1/invites", headers=ACCOUNT, json={"email": "x@dop.local"})
        assert r.status_code == http
        assert r.json()["detail"] == "came from the core"

    @pytest.mark.parametrize(("code", "http"), CASOS)
    def test_erro_no_resolver_vira_status_http(self, client, core, code, http):
        core.EnsureUser.fails_with(code)
        assert client.get("/api/v1/me", headers=ACCOUNT).status_code == http

    def test_erro_interno_da_rota_nao_vaza_detalhe(self, client, core):
        core.CreateInvite.fails_with(grpc.StatusCode.INTERNAL, "senha do banco no log")
        r = client.post("/api/v1/invites", headers=ACCOUNT, json={"email": "x@dop.local"})
        assert r.status_code == 500
        assert "senha" not in r.text

    def test_erro_interno_do_resolver_tambem_nao_vaza(self, client, core):
        """O middleware traduz na mão — e precisa redigir igual ao handler."""
        core.EnsureUser.fails_with(grpc.StatusCode.INTERNAL, "senha do banco no log")
        r = client.get("/api/v1/me", headers=ACCOUNT)
        assert r.status_code == 500
        assert "senha" not in r.text


class TestRotasProtegidas:
    def test_sem_conta_ativa_da_400(self, client):
        """Regra do SP-0, cobrada pelo @account_scoped na borda."""
        r = client.get("/api/v1/accounts/current/members", headers=SEM_CONTA)
        assert r.status_code == 400

    def test_papel_insuficiente_da_403(self, client, core):
        core.ListMemberships.returns(
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
        assert client.get("/api/v1/accounts/current/members", headers=ACCOUNT).status_code == 403

    def test_sem_token_da_401(self, client):
        assert client.get("/api/v1/accounts").status_code == 401


class TestListagens:
    def test_accounts_traduz_o_proto(self, client):
        r = client.get("/api/v1/accounts", headers=ACCOUNT)
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

    def test_members_traduz_o_papel_para_string(self, client):
        r = client.get("/api/v1/accounts/current/members", headers=ACCOUNT)
        assert r.status_code == 200
        assert r.json() == [{"id": "m-1", "user_id": "u-1", "role": "admin"}]


class TestEscritas:
    def test_cria_organizacao_com_chave_de_idempotencia(self, client, core):
        r = client.post(
            "/api/v1/accounts",
            headers=ACCOUNT,
            json={"handle": "acme", "display_name": "ACME", "legal_id": "00.000.000/0001-00"},
        )
        assert r.status_code == 201
        req = core.CreateAccount.last["request"]
        assert req.kind == identity_pb2.Account.KIND_ORGANIZATION
        assert req.handle == "acme"
        assert req.idempotency_key  # repetir não pode duplicar (ADR-0017)

    def test_criar_organizacao_exige_conta_ativa(self, client):
        r = client.post(
            "/api/v1/accounts", headers=SEM_CONTA, json={"handle": "a", "display_name": "A"}
        )
        assert r.status_code == 400

    def test_cria_convite_com_papel_e_concessoes(self, client, core):
        r = client.post(
            "/api/v1/invites",
            headers=ACCOUNT,
            json={
                "email": "novo@dop.local",
                "role": "developer",
                "grants": [{"resource_id": "res-1", "level": "use"}],
            },
        )
        assert r.status_code == 201
        assert r.json()["status"] == "pending"
        req = core.CreateInvite.last["request"]
        assert req.role == identity_pb2.ROLE_DEVELOPER
        assert req.grants[0].resource.id == "res-1"
        assert req.grants[0].level == "use"
        assert req.ctx.account.id == "acct-1"

    def test_convite_exige_papel_de_gestao(self, monkeypatch, core):
        core.ListMemberships.returns(
            identity_pb2.ListMembershipsResponse(
                memberships=[
                    identity_pb2.Membership(id="m-1", role=identity_pb2.ROLE_DEVELOPER)
                ]
            )
        )
        with TestClient(create_app()) as c:
            r = c.post("/api/v1/invites", headers=ACCOUNT, json={"email": "x@dop.local"})
        assert r.status_code == 403
        assert not core.CreateInvite.calls
