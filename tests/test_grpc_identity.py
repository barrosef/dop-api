"""The BFF's gRPC port, against the same fake core as the REST routes.

What is checked here is not the core, but that the SECOND transport behaves like
the first: the same authentication, the same authorization, the same error
translation — and, at the end of the file, literally the same result.
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
# The same data, in the other transport's clothes — used in the parity proof.
REST_HEADERS = {"authorization": token_for(), "x-account-id": "acct-1"}

# Enum → string, to compare the gRPC response with REST's JSON.
KIND_PARA_NOME = {value: name for name, value in convert.KIND_FROM_NAME.items()}


async def _err(call) -> AioRpcError:
    with pytest.raises(AioRpcError) as exc:
        await call
    return exc.value


class TestHappyPath:
    """Um teste por RPC do IdentityService."""

    async def test_get_me_brings_the_cores_user_id(self, stub_grpc):
        me = await stub_grpc.GetMe(bff.GetMeRequest(), metadata=ACCOUNT)
        assert me.user_id == "u-1"  # the core's id, not the subject
        assert me.subject == "sub-1"
        assert me.email == "dev@dop.local"
        assert me.account_id == "acct-1"
        assert me.role == bff.ROLE_ADMIN
        assert list(me.providers) == ["email", "password"]

    async def test_list_accounts_aggregates_the_role_in_the_active_account(self, stub_grpc):
        resp = await stub_grpc.ListAccounts(bff.ListAccountsRequest(), metadata=ACCOUNT)
        assert len(resp.accounts) == 1
        account = resp.accounts[0]
        assert account.id == "acct-1"
        assert account.handle == "acme"
        assert account.display_name == "ACME"
        assert account.kind == bff.AccountSummary.KIND_ORGANIZATION
        # A role does not exist on the core's Account — it comes from the
        # membership. Joining the two is what this edge contract does
        # differently.
        assert account.role == bff.ROLE_ADMIN

    async def test_list_members_translates_the_role_into_an_enum(self, stub_grpc):
        resp = await stub_grpc.ListMembers(bff.ListMembersRequest(), metadata=ACCOUNT)
        assert [(m.id, m.user_id, m.role) for m in resp.members] == [
            ("m-1", "u-1", bff.ROLE_ADMIN)
        ]

    async def test_create_account_uses_the_clients_idempotency_key(
        self, stub_grpc, core
    ):
        """Over gRPC the one that sends the key is the CLIENT — it is the one that knows whether it is a retry."""
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
        assert account.role == bff.ROLE_OWNER  # whoever creates the organization owns it
        req = core.CreateAccount.last["request"]
        assert req.kind == identity_pb2.Account.KIND_ORGANIZATION
        assert req.handle == "acme"
        assert req.idempotency_key == "key-do-client"

    async def test_create_account_with_no_key_the_bff_generates_one(self, stub_grpc, core):
        await stub_grpc.CreateAccount(
            bff.CreateAccountRequest(handle="acme", display_name="ACME"), metadata=ACCOUNT
        )
        assert core.CreateAccount.last["request"].idempotency_key  # ADR-0017

    async def test_create_invite_carries_the_role_and_the_grants(self, stub_grpc, core):
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

    async def test_an_omitted_role_falls_to_the_same_default_as_rest(self, stub_grpc, core):
        """ROLE_UNSPECIFIED does not become an empty role: it becomes the use case's default."""
        await stub_grpc.CreateInvite(
            bff.CreateInviteRequest(email="novo@dop.local"), metadata=ACCOUNT
        )
        assert core.CreateInvite.last["request"].role == identity_pb2.ROLE_DEVELOPER


class TestContextPropagation:
    async def test_the_metadata_from_bff_to_core_carries_account_actor_and_trace(self, stub_grpc, core):
        await stub_grpc.ListAccounts(
            bff.ListAccountsRequest(),
            metadata=metadata_for(token_for(), **{"x-request-id": "trace-grpc"}),
        )
        md = core.ListAccounts.metadata()
        assert md["x-account-id"] == "acct-1"
        assert md["x-actor-id"] == "u-1"
        assert md["x-actor-kind"] == "user"
        assert md["x-request-id"] == "trace-grpc"  # mesmo rastro das duas pontas

    async def test_the_call_context_goes_in_the_body_of_the_call_to_the_core(self, stub_grpc, core):
        await stub_grpc.ListMembers(bff.ListMembersRequest(), metadata=ACCOUNT)
        req = core.ListMemberships.last["request"]
        assert req.ctx.account.id == "acct-1"
        assert req.ctx.actor.id == "u-1"


class TestAuthentication:
    async def test_with_no_token_it_gives_unauthenticated(self, stub_grpc):
        exc = await _err(
            stub_grpc.GetMe(bff.GetMeRequest(), metadata=metadata_for(token=None))
        )
        assert exc.code() == grpc.StatusCode.UNAUTHENTICATED

    async def test_with_no_metadata_at_all_it_gives_unauthenticated(self, stub_grpc):
        exc = await _err(stub_grpc.ListAccounts(bff.ListAccountsRequest()))
        assert exc.code() == grpc.StatusCode.UNAUTHENTICATED

    async def test_a_malformed_token_gives_unauthenticated(self, stub_grpc):
        exc = await _err(
            stub_grpc.GetMe(bff.GetMeRequest(), metadata=metadata_for("Bearer lixo"))
        )
        assert exc.code() == grpc.StatusCode.UNAUTHENTICATED
        assert "malformed" in exc.details()


class TestAuthorization:
    """Os MESMOS decorators do REST, cobrando as mesmas regras."""

    async def test_no_membership_in_the_account_gives_permission_denied(self, stub_grpc, core):
        """Asking for an account you are not a member of is not NOT_FOUND: it is a refusal."""
        core.ListAccounts.returns(
            identity_pb2.ListAccountsResponse(
                accounts=[identity_pb2.Account(id="acct-de-outro")]
            )
        )
        exc = await _err(stub_grpc.GetMe(bff.GetMeRequest(), metadata=ACCOUNT))
        assert exc.code() == grpc.StatusCode.PERMISSION_DENIED
        assert "membership" in exc.details()

    async def test_an_insufficient_role_gives_permission_denied(self, stub_grpc, core):
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

    async def test_with_no_active_account_it_gives_invalid_argument(self, stub_grpc):
        """Regra do SP-0 (@account_scoped): 400 no REST, INVALID_ARGUMENT aqui."""
        exc = await _err(
            stub_grpc.ListMembers(bff.ListMembersRequest(), metadata=SEM_CONTA)
        )
        assert exc.code() == grpc.StatusCode.INVALID_ARGUMENT

    async def test_login_with_no_active_account_still_works(self, stub_grpc):
        """It is how the client loads the account selector."""
        me = await stub_grpc.GetMe(bff.GetMeRequest(), metadata=SEM_CONTA)
        assert me.user_id == "u-1"
        assert me.account_id == ""
        assert me.role == bff.ROLE_UNSPECIFIED


class TestErrorTranslation:
    """The core's status crosses; a 5xx detail does not."""

    CASOS = [
        grpc.StatusCode.NOT_FOUND,
        grpc.StatusCode.PERMISSION_DENIED,
        grpc.StatusCode.INVALID_ARGUMENT,
        grpc.StatusCode.ALREADY_EXISTS,
    ]

    @pytest.mark.parametrize("code", CASOS)
    async def test_the_cores_status_crosses_intact(self, stub_grpc, core, code):
        core.CreateInvite.fails_with(code)
        exc = await _err(
            stub_grpc.CreateInvite(
                bff.CreateInviteRequest(email="x@dop.local"), metadata=ACCOUNT
            )
        )
        assert exc.code() == code
        assert exc.details() == "came from the core"

    async def test_an_internal_from_the_core_leaks_no_detail(self, stub_grpc, core):
        core.CreateInvite.fails_with(grpc.StatusCode.INTERNAL, "senha do banco no log")
        exc = await _err(
            stub_grpc.CreateInvite(
                bff.CreateInviteRequest(email="x@dop.local"), metadata=ACCOUNT
            )
        )
        assert exc.code() == grpc.StatusCode.INTERNAL
        assert exc.details() == "internal error"
        assert "senha" not in exc.details()

    async def test_an_internal_in_the_resolver_does_not_leak_either(self, stub_grpc, core):
        """The auth interceptor translates by hand — and has to write it the same way."""
        core.EnsureUser.fails_with(grpc.StatusCode.INTERNAL, "senha do banco no log")
        exc = await _err(stub_grpc.GetMe(bff.GetMeRequest(), metadata=ACCOUNT))
        assert exc.code() == grpc.StatusCode.INTERNAL
        assert "senha" not in exc.details()

    async def test_a_core_that_is_down_crosses_but_with_no_detail(self, stub_grpc, core):
        """UNAVAILABLE is a 503 — the 5xx class, so the detail is redacted too.

        The same rule as REST: the status tells the client what to do (retry),
        the core's detail may carry a host or a credential and does not leave
        here.
        """
        core.EnsureUser.fails_with(grpc.StatusCode.UNAVAILABLE, "pgbouncer://user:senha@db")
        exc = await _err(stub_grpc.GetMe(bff.GetMeRequest(), metadata=ACCOUNT))
        assert exc.code() == grpc.StatusCode.UNAVAILABLE
        assert "senha" not in exc.details()

    async def test_an_invalid_message_becomes_invalid_argument(self, stub_grpc):
        """email com menos de 3 caracteres: 422 no REST, INVALID_ARGUMENT aqui."""
        exc = await _err(
            stub_grpc.CreateInvite(bff.CreateInviteRequest(email="x"), metadata=ACCOUNT)
        )
        assert exc.code() == grpc.StatusCode.INVALID_ARGUMENT


class TestParityBetweenTransports:
    """The proof that there is no duplicated logic: one function, two adapters.

    If anybody reimplements a use case in the servicer (or in the router), these
    tests break — which is the only way for the duplication not to go unnoticed
    in a review.
    """

    @staticmethod
    def _account_as_json(account: bff.AccountSummary) -> dict:
        """Normalizes the gRPC response into the SAME shape REST returns."""
        return {
            "id": account.id,
            "handle": account.handle,
            "display_name": account.display_name,
            "kind": KIND_PARA_NOME.get(account.kind, ""),
            "role": convert.role_name(account.role),
        }

    async def test_list_accounts_gives_the_same_result_on_both_ports(
        self, client, stub_grpc, core
    ):
        rest = client.get("/api/v1/accounts", headers=REST_HEADERS).json()
        grpc_resp = await stub_grpc.ListAccounts(bff.ListAccountsRequest(), metadata=ACCOUNT)
        assert [self._account_as_json(c) for c in grpc_resp.accounts] == rest

        # And the two calls made exactly the same request to the core: it is the
        # same code building the request, not two copies that happen to match.
        requests = [c["request"] for c in core.ListAccounts.calls[-2:]]
        assert requests[0] == requests[1]

    async def test_me_gives_the_same_result_on_both_ports(self, client, stub_grpc):
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

    async def test_members_gives_the_same_result_on_both_ports(self, client, stub_grpc):
        rest = client.get("/api/v1/accounts/current/members", headers=REST_HEADERS).json()
        resp = await stub_grpc.ListMembers(bff.ListMembersRequest(), metadata=ACCOUNT)
        assert [
            {"id": m.id, "user_id": m.user_id, "role": convert.role_name(m.role)}
            for m in resp.members
        ] == rest

    async def test_an_insufficient_role_is_refused_on_both_ports(self, client, stub_grpc, core):
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


class TestStructuredLogging:
    """Log agregado só serve se as duas portas falarem a mesma língua."""

    async def test_the_grpc_port_writes_the_same_json_as_rest(self, stub_grpc, capsys):
        # O `configure()` roda no lifespan do FastAPI; aqui o server gRPC sobe
        # on its own, so we configure it by hand — the same `configure`, not another.
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
        # `method=grpc` and `path` with the full method: it is what allows
        # separating
        # os transportes num log onde as duas portas escrevem no mesmo lugar.
        assert entry["method"] == "grpc"
        assert entry["path"] == "/dop.bff.v1.IdentityService/GetMe"

    async def test_the_token_does_not_appear_in_the_log(self, stub_grpc, capsys):
        """Redacting a secret is a requirement (F-10), and it holds on both transports."""
        configure_logging()
        await stub_grpc.GetMe(bff.GetMeRequest(), metadata=ACCOUNT)
        assert "Bearer" not in capsys.readouterr().out


class TestPublicOverGrpc:
    """@public holds on both transports — the marker is the same."""

    async def test_the_marker_crosses_the_other_decorators(self):
        """functools.wraps copies the __dict__, so the stacking order does not matter."""

        @log
        @public
        async def rpc(request, context):
            return "ok"

        interceptor = AuthInterceptor(verifier=None)
        # Handler público sai do interceptor INTACTO: nem token, nem resolver.
        assert interceptor._wrap(rpc, None) is rpc

    async def test_an_ordinary_handler_is_wrapped(self):
        @log
        async def rpc(request, context):
            return "ok"

        assert AuthInterceptor(verifier=None)._wrap(rpc, None) is not rpc
