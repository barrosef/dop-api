"""A porta gRPC do BFF, contra o mesmo núcleo double das rotas REST.

O que se verifica aqui não é o núcleo, e sim que o SEGUNDO transporte se
comporta como o primeiro: mesma autenticação, mesma autorização, mesma
tradução de err — e, no fim do arquivo, literalmente o mesmo result.
"""

import json

import grpc
import pytest
from grpc.aio import AioRpcError

from app.coreclient.gen.dop.v1 import common_pb2, identity_pb2
from app.grpcapi import convert
from app.grpcapi.gen.dop.bff.v1 import identity_pb2 as bff
from app.grpcapi.interceptors import AuthInterceptor
from app.platform.logging.config import configure as configure_logging
from app.platform.logging.decorator import log
from app.platform.security.decorator import public
from tests.conftest import metadata_for, token_for

ACCOUNT = metadata_for(token_for())
SEM_CONTA = metadata_for(token_for(), account_id="")
# Os mesmos data, na roupa do outro transporte — usados na prova de paridade.
REST_HEADERS = {"authorization": token_for(), "x-account-id": "acct-1"}

# Enum → string, para comparar a response gRPC com o JSON do REST.
KIND_PARA_NOME = {value: name for name, value in convert.KIND_FROM_NAME.items()}


async def _err(call) -> AioRpcError:
    with pytest.raises(AioRpcError) as exc:
        await call
    return exc.value


class TestCaminhoFeliz:
    """Um teste por RPC do IdentityService."""

    async def test_get_me_traz_o_user_id_do_core(self, stub_grpc):
        me = await stub_grpc.GetMe(bff.GetMeRequest(), metadata=ACCOUNT)
        assert me.user_id == "u-1"  # id do core, não o subject
        assert me.subject == "sub-1"
        assert me.email == "dev@dop.local"
        assert me.account_id == "acct-1"
        assert me.role == bff.ROLE_ADMIN
        assert list(me.providers) == ["email", "password"]

    async def test_list_accounts_agrega_o_papel_na_conta_ativa(self, stub_grpc):
        resp = await stub_grpc.ListAccounts(bff.ListAccountsRequest(), metadata=ACCOUNT)
        assert len(resp.accounts) == 1
        account = resp.accounts[0]
        assert account.id == "acct-1"
        assert account.handle == "acme"
        assert account.display_name == "ACME"
        assert account.kind == bff.AccountSummary.KIND_ORGANIZATION
        # Papel não existe no Account do núcleo — vem do vínculo. Juntar os dois
        # é o que este contrato de borda faz de diferente.
        assert account.role == bff.ROLE_ADMIN

    async def test_list_members_traduz_o_papel_para_enum(self, stub_grpc):
        resp = await stub_grpc.ListMembers(bff.ListMembersRequest(), metadata=ACCOUNT)
        assert [(m.id, m.user_id, m.role) for m in resp.members] == [
            ("m-1", "u-1", bff.ROLE_ADMIN)
        ]

    async def test_create_account_usa_a_chave_de_idempotencia_do_cliente(
        self, stub_grpc, core
    ):
        """No gRPC quem manda a key é o CLIENTE — é ele que sabe se é retry."""
        account = await stub_grpc.CreateAccount(
            bff.CreateAccountRequest(
                handle="acme",
                display_name="ACME",
                legal_id="00.000.000/0001-00",
                idempotency_key="key-do-client",
            ),
            metadata=ACCOUNT,
        )
        assert account.id == "acct-1"
        assert account.role == bff.ROLE_OWNER  # quem cria a organização é o dono
        req = core.CreateAccount.last["request"]
        assert req.kind == identity_pb2.Account.KIND_ORGANIZATION
        assert req.handle == "acme"
        assert req.idempotency_key == "key-do-client"

    async def test_create_account_sem_chave_o_bff_gera_uma(self, stub_grpc, core):
        await stub_grpc.CreateAccount(
            bff.CreateAccountRequest(handle="acme", display_name="ACME"), metadata=ACCOUNT
        )
        assert core.CreateAccount.last["request"].idempotency_key  # ADR-0017

    async def test_create_invite_leva_papel_e_concessoes(self, stub_grpc, core):
        convite = await stub_grpc.CreateInvite(
            bff.CreateInviteRequest(
                email="novo@dop.local",
                role=bff.ROLE_DEVELOPER,
                grants=[bff.ResourceGrantSpec(resource_id="res-1", level="use")],
            ),
            metadata=ACCOUNT,
        )
        assert convite.status == bff.InviteSummary.STATUS_PENDING
        assert convite.role == bff.ROLE_DEVELOPER
        req = core.CreateInvite.last["request"]
        assert req.role == identity_pb2.ROLE_DEVELOPER
        assert req.grants[0].resource.id == "res-1"
        assert req.grants[0].level == "use"
        assert req.ctx.account.id == "acct-1"

    async def test_papel_omitido_cai_no_mesmo_padrao_do_rest(self, stub_grpc, core):
        """ROLE_UNSPECIFIED não vira role vazio: vira o padrão do caso de uso."""
        await stub_grpc.CreateInvite(
            bff.CreateInviteRequest(email="novo@dop.local"), metadata=ACCOUNT
        )
        assert core.CreateInvite.last["request"].role == identity_pb2.ROLE_DEVELOPER


class TestPropagacaoDeContexto:
    async def test_metadado_do_bff_ao_core_leva_conta_ator_e_rastro(self, stub_grpc, core):
        await stub_grpc.ListAccounts(
            bff.ListAccountsRequest(),
            metadata=metadata_for(token_for(), **{"x-request-id": "trace-grpc"}),
        )
        md = core.ListAccounts.metadata()
        assert md["x-account-id"] == "acct-1"
        assert md["x-actor-id"] == "u-1"
        assert md["x-actor-kind"] == "user"
        assert md["x-request-id"] == "trace-grpc"  # mesmo rastro das duas pontas

    async def test_call_context_vai_no_corpo_da_chamada_ao_core(self, stub_grpc, core):
        await stub_grpc.ListMembers(bff.ListMembersRequest(), metadata=ACCOUNT)
        req = core.ListMemberships.last["request"]
        assert req.ctx.account.id == "acct-1"
        assert req.ctx.actor.id == "u-1"


class TestAutenticacao:
    async def test_sem_token_da_unauthenticated(self, stub_grpc):
        exc = await _err(
            stub_grpc.GetMe(bff.GetMeRequest(), metadata=metadata_for(token=None))
        )
        assert exc.code() == grpc.StatusCode.UNAUTHENTICATED

    async def test_sem_metadado_nenhum_da_unauthenticated(self, stub_grpc):
        exc = await _err(stub_grpc.ListAccounts(bff.ListAccountsRequest()))
        assert exc.code() == grpc.StatusCode.UNAUTHENTICATED

    async def test_token_malformado_da_unauthenticated(self, stub_grpc):
        exc = await _err(
            stub_grpc.GetMe(bff.GetMeRequest(), metadata=metadata_for("Bearer lixo"))
        )
        assert exc.code() == grpc.StatusCode.UNAUTHENTICATED
        assert "malformed" in exc.details()


class TestAutorizacao:
    """Os MESMOS decorators do REST, cobrando as mesmas regras."""

    async def test_sem_vinculo_com_a_conta_da_permission_denied(self, stub_grpc, core):
        """Pedir uma account de que não se é membro não é NOT_FOUND: é recusa."""
        core.ListAccounts.returns(
            identity_pb2.ListAccountsResponse(
                accounts=[identity_pb2.Account(id="acct-de-outro")]
            )
        )
        exc = await _err(stub_grpc.GetMe(bff.GetMeRequest(), metadata=ACCOUNT))
        assert exc.code() == grpc.StatusCode.PERMISSION_DENIED
        assert "membership" in exc.details()

    async def test_papel_insuficiente_da_permission_denied(self, stub_grpc, core):
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
        exc = await _err(stub_grpc.ListMembers(bff.ListMembersRequest(), metadata=ACCOUNT))
        assert exc.code() == grpc.StatusCode.PERMISSION_DENIED
        assert not core.CreateInvite.calls

    async def test_sem_conta_ativa_da_invalid_argument(self, stub_grpc):
        """Regra do SP-0 (@account_scoped): 400 no REST, INVALID_ARGUMENT aqui."""
        exc = await _err(
            stub_grpc.ListMembers(bff.ListMembersRequest(), metadata=SEM_CONTA)
        )
        assert exc.code() == grpc.StatusCode.INVALID_ARGUMENT

    async def test_login_sem_conta_ativa_ainda_funciona(self, stub_grpc):
        """É assim que o client carrega o seletor de contas."""
        me = await stub_grpc.GetMe(bff.GetMeRequest(), metadata=SEM_CONTA)
        assert me.user_id == "u-1"
        assert me.account_id == ""
        assert me.role == bff.ROLE_UNSPECIFIED


class TestTraducaoDeErro:
    """O status do núcleo atravessa; o detalhe de 5xx, não."""

    CASOS = [
        grpc.StatusCode.NOT_FOUND,
        grpc.StatusCode.PERMISSION_DENIED,
        grpc.StatusCode.INVALID_ARGUMENT,
        grpc.StatusCode.ALREADY_EXISTS,
    ]

    @pytest.mark.parametrize("code", CASOS)
    async def test_status_do_nucleo_atravessa_intacto(self, stub_grpc, core, code):
        core.CreateInvite.fails_with(code)
        exc = await _err(
            stub_grpc.CreateInvite(
                bff.CreateInviteRequest(email="x@dop.local"), metadata=ACCOUNT
            )
        )
        assert exc.code() == code
        assert exc.details() == "came from the core"

    async def test_internal_do_nucleo_nao_vaza_detalhe(self, stub_grpc, core):
        core.CreateInvite.fails_with(grpc.StatusCode.INTERNAL, "senha do banco no log")
        exc = await _err(
            stub_grpc.CreateInvite(
                bff.CreateInviteRequest(email="x@dop.local"), metadata=ACCOUNT
            )
        )
        assert exc.code() == grpc.StatusCode.INTERNAL
        assert exc.details() == "internal error"
        assert "senha" not in exc.details()

    async def test_internal_no_resolver_tambem_nao_vaza(self, stub_grpc, core):
        """O interceptor de auth traduz na mão — e precisa redigir igual."""
        core.EnsureUser.fails_with(grpc.StatusCode.INTERNAL, "senha do banco no log")
        exc = await _err(stub_grpc.GetMe(bff.GetMeRequest(), metadata=ACCOUNT))
        assert exc.code() == grpc.StatusCode.INTERNAL
        assert "senha" not in exc.details()

    async def test_nucleo_fora_do_ar_atravessa_mas_sem_detalhe(self, stub_grpc, core):
        """UNAVAILABLE é 503 — classe 5xx, então o detalhe é redigido também.

        Mesma regra do REST: o status diz ao client o que fazer (retentar), o
        detalhe do núcleo pode carregar host ou credencial e não sai daqui.
        """
        core.EnsureUser.fails_with(grpc.StatusCode.UNAVAILABLE, "pgbouncer://user:senha@db")
        exc = await _err(stub_grpc.GetMe(bff.GetMeRequest(), metadata=ACCOUNT))
        assert exc.code() == grpc.StatusCode.UNAVAILABLE
        assert "senha" not in exc.details()

    async def test_mensagem_invalida_vira_invalid_argument(self, stub_grpc):
        """email com menos de 3 caracteres: 422 no REST, INVALID_ARGUMENT aqui."""
        exc = await _err(
            stub_grpc.CreateInvite(bff.CreateInviteRequest(email="x"), metadata=ACCOUNT)
        )
        assert exc.code() == grpc.StatusCode.INVALID_ARGUMENT


class TestParidadeEntreTransportes:
    """A prova de que não há lógica duplicada: uma função, dois adaptadores.

    Se alguém reimplementar um caso de uso no servicer (ou no router), estes
    testes quebram — que é a única maneira de a duplicação não passar
    despercebida numa revisão.
    """

    @staticmethod
    def _conta_como_json(account: bff.AccountSummary) -> dict:
        """Normaliza a response gRPC para a MESMA forma que o REST returns."""
        return {
            "id": account.id,
            "handle": account.handle,
            "display_name": account.display_name,
            "kind": KIND_PARA_NOME.get(account.kind, ""),
            "role": convert.role_name(account.role),
        }

    async def test_list_accounts_da_o_mesmo_resultado_nas_duas_portas(
        self, client, stub_grpc, core
    ):
        rest = client.get("/api/v1/accounts", headers=REST_HEADERS).json()
        grpc_resp = await stub_grpc.ListAccounts(bff.ListAccountsRequest(), metadata=ACCOUNT)
        assert [self._conta_como_json(c) for c in grpc_resp.accounts] == rest

        # E as duas calls fizeram ao núcleo exatamente o mesmo request: é o
        # mesmo código montando a requisição, não duas cópias que combinam.
        requests = [c["request"] for c in core.ListAccounts.calls[-2:]]
        assert requests[0] == requests[1]

    async def test_me_da_o_mesmo_resultado_nas_duas_portas(self, client, stub_grpc):
        rest = client.get("/api/v1/me", headers=REST_HEADERS).json()
        me = await stub_grpc.GetMe(bff.GetMeRequest(), metadata=ACCOUNT)
        assert {
            "subject": me.subject,
            "user_id": me.user_id,
            "email": me.email,
            "name": me.name,
            "providers": list(me.providers),
            "account_id": me.account_id,
            "role": convert.role_name(me.role),
        } == rest

    async def test_members_da_o_mesmo_resultado_nas_duas_portas(self, client, stub_grpc):
        rest = client.get("/api/v1/accounts/current/members", headers=REST_HEADERS).json()
        resp = await stub_grpc.ListMembers(bff.ListMembersRequest(), metadata=ACCOUNT)
        assert [
            {"id": m.id, "user_id": m.user_id, "role": convert.role_name(m.role)}
            for m in resp.members
        ] == rest

    async def test_papel_insuficiente_recusa_nas_duas_portas(self, client, stub_grpc, core):
        """Mesma decisão, vocabulários diferentes: 403 no HTTP, PERMISSION_DENIED no gRPC."""
        core.ListMemberships.returns(
            identity_pb2.ListMembershipsResponse(
                memberships=[
                    identity_pb2.Membership(
                        id="m-1",
                        user=common_pb2.UserRef(id="u-1"),
                        role=identity_pb2.ROLE_VIEWER,
                    )
                ]
            )
        )
        assert (
            client.get("/api/v1/accounts/current/members", headers=REST_HEADERS).status_code
            == 403
        )
        exc = await _err(stub_grpc.ListMembers(bff.ListMembersRequest(), metadata=ACCOUNT))
        assert exc.code() == grpc.StatusCode.PERMISSION_DENIED


class TestLogEstruturado:
    """Log agregado só serve se as duas portas falarem a mesma língua."""

    async def test_a_porta_grpc_escreve_o_mesmo_json_do_rest(self, stub_grpc, capsys):
        # O `configure()` roda no lifespan do FastAPI; aqui o server gRPC sobe
        # sozinho, então configuramos à mão — o mesmo `configure`, não outro.
        configure_logging()
        await stub_grpc.GetMe(bff.GetMeRequest(), metadata=ACCOUNT)

        linhas = [
            json.loads(linha)
            for linha in capsys.readouterr().out.splitlines()
            if linha.startswith("{")
        ]
        req = [linha for linha in linhas if linha.get("event") == "request"]
        assert req, "faltou a linha de request"
        entry = req[0]
        for campo in ("ts", "level", "component", "request_id", "duration_ms", "status"):
            assert campo in entry, f"campo canônico ausente: {campo}"
        assert entry["component"] == "dop-api"
        # `method=grpc` e `path` com o método completo: é o que permite separar
        # os transportes num log onde as duas portas escrevem no mesmo lugar.
        assert entry["method"] == "grpc"
        assert entry["path"] == "/dop.bff.v1.IdentityService/GetMe"

    async def test_o_token_nao_aparece_no_log(self, stub_grpc, capsys):
        """Redação de segredo é requisito (F-10), e vale nos dois transportes."""
        configure_logging()
        await stub_grpc.GetMe(bff.GetMeRequest(), metadata=ACCOUNT)
        assert "Bearer" not in capsys.readouterr().out


class TestPublicNoGrpc:
    """@public vale nos dois transportes — o marcador é o mesmo."""

    async def test_marcador_atravessa_os_outros_decorators(self):
        """functools.wraps copia o __dict__, então a ordem de empilhamento não importa."""

        @log
        @public
        async def rpc(request, context):
            return "ok"

        interceptor = AuthInterceptor(verifier=None)
        # Handler público sai do interceptor INTACTO: nem token, nem resolver.
        assert interceptor._wrap(rpc, None) is rpc

    async def test_handler_comum_e_envolvido(self):
        @log
        async def rpc(request, context):
            return "ok"

        assert AuthInterceptor(verifier=None)._wrap(rpc, None) is not rpc
