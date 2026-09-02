"""Identity routes against a fake core.

What is checked here is not the core (it has its own tests) but the EDGE: that
the call context goes out correctly in the metadata, that the core's error
semantics cross intact, and that the decorators block what they should block.
"""

import grpc
import pytest
from fastapi.testclient import TestClient

from app.coreclient.gen.dop.v1 import common_pb2, identity_pb2
from app.main import create_app
from tests.conftest import token_for

ACCOUNT = {"authorization": token_for(), "x-account-id": "acct-1"}
SEM_CONTA = {"authorization": token_for()}


class TestTheAuthorizationResolver:
    def test_ensure_user_runs_on_every_login(self, client, core):
        """Idempotent by design: it is not only on the first access."""
        client.get("/api/v1/me", headers=ACCOUNT)
        client.get("/api/v1/me", headers=ACCOUNT)
        assert len(core.EnsureUser.calls) == 2
        req = core.EnsureUser.last["request"]
        assert req.subject == "sub-1"
        assert req.email == "dev@dop.local"
        assert req.provider == "email"  # normalized, not a Firebase claim
        assert req.idempotency_key

    def test_me_brings_the_cores_user_id(self, client):
        r = client.get("/api/v1/me", headers=ACCOUNT)
        assert r.status_code == 200
        body = r.json()
        assert body["user_id"] == "u-1"  # the core's id, not the subject
        assert body["subject"] == "sub-1"
        assert body["role"] == "admin"
        assert body["account_id"] == "acct-1"

    def test_no_membership_in_the_account_gives_a_403(self, client, core):
        """Asking for an account you are not a member of is not a 404: it is a 403."""
        core.ListAccounts.returns(
            identity_pb2.ListAccountsResponse(
                accounts=[identity_pb2.Account(id="acct-de-outro")]
            )
        )
        r = client.get("/api/v1/me", headers=ACCOUNT)
        assert r.status_code == 403

    def test_an_unresolved_role_does_not_bring_the_request_down(self, client, core):
        """An unavailable ListMemberships degrades to an empty role, not to a 500.

        The membership has already been proven by ListAccounts; all that is
        missing is the role — and whoever requires a role refuses later, with a
        403.
        """
        core.ListMemberships.fails_with(grpc.StatusCode.UNIMPLEMENTED)
        r = client.get("/api/v1/me", headers=ACCOUNT)
        assert r.status_code == 200
        assert r.json()["role"] == ""

        r = client.get("/api/v1/accounts/current/members", headers=ACCOUNT)
        assert r.status_code == 403

    def test_a_core_that_is_down_becomes_a_503(self, client, core):
        core.EnsureUser.fails_with(grpc.StatusCode.UNAVAILABLE)
        assert client.get("/api/v1/me", headers=ACCOUNT).status_code == 503

    def test_with_no_active_account_login_still_works(self, client, core):
        """It is how the cockpit loads the account selector."""
        r = client.get("/api/v1/me", headers=SEM_CONTA)
        assert r.status_code == 200
        assert r.json()["user_id"] == "u-1"
        assert not core.ListMemberships.calls


class TestContextPropagation:
    def test_the_metadata_carries_the_active_account_and_the_actor(self, client, core):
        client.get("/api/v1/accounts", headers={**ACCOUNT, "x-request-id": "trace-1"})
        md = core.ListAccounts.metadata()
        assert md["x-account-id"] == "acct-1"
        assert md["x-actor-id"] == "u-1"
        assert md["x-actor-kind"] == "user"
        assert md["x-request-id"] == "trace-1"  # mesmo rastro do BFF ao core

    def test_the_call_context_goes_in_the_requests_body(self, client, core):
        client.get("/api/v1/accounts/current/members", headers=ACCOUNT)
        req = core.ListMemberships.last["request"]
        assert req.ctx.account.id == "acct-1"
        assert req.ctx.actor.id == "u-1"
        assert req.account.id == "acct-1"

    def test_the_call_carries_a_deadline(self, client, core):
        client.get("/api/v1/accounts", headers=ACCOUNT)
        assert core.ListAccounts.last["timeout"] == 10.0


class TestErrorTranslation:
    """The BFF translates the core's semantics — it does not invent its own.

    Two independent paths have to translate the same way: the application's
    handler (an error inside the route) and as_http (an error inside the
    middleware, which runs ABOVE Starlette's ExceptionMiddleware and would not
    be reached by the handler).
    """

    CASOS = [
        (grpc.StatusCode.NOT_FOUND, 404),
        (grpc.StatusCode.PERMISSION_DENIED, 403),
        (grpc.StatusCode.INVALID_ARGUMENT, 400),
        (grpc.StatusCode.ALREADY_EXISTS, 409),
    ]

    @pytest.mark.parametrize(("code", "http"), CASOS)
    def test_an_error_in_the_route_becomes_an_http_status(self, client, core, code, http):
        core.CreateInvite.fails_with(code)
        r = client.post("/api/v1/invites", headers=ACCOUNT, json={"email": "x@dop.local"})
        assert r.status_code == http
        assert r.json()["detail"] == "came from the core"

    @pytest.mark.parametrize(("code", "http"), CASOS)
    def test_an_error_in_the_resolver_becomes_an_http_status(self, client, core, code, http):
        core.EnsureUser.fails_with(code)
        assert client.get("/api/v1/me", headers=ACCOUNT).status_code == http

    def test_an_internal_error_in_the_route_leaks_no_detail(self, client, core):
        core.CreateInvite.fails_with(grpc.StatusCode.INTERNAL, "senha do banco no log")
        r = client.post("/api/v1/invites", headers=ACCOUNT, json={"email": "x@dop.local"})
        assert r.status_code == 500
        assert "senha" not in r.text

    def test_an_internal_error_in_the_resolver_does_not_leak_either(self, client, core):
        """The middleware translates by hand — and has to write it just like the handler."""
        core.EnsureUser.fails_with(grpc.StatusCode.INTERNAL, "senha do banco no log")
        r = client.get("/api/v1/me", headers=ACCOUNT)
        assert r.status_code == 500
        assert "senha" not in r.text


class TestProtectedRoutes:
    def test_with_no_active_account_it_gives_a_400(self, client):
        """SP-0's rule, enforced by @account_scoped at the edge."""
        r = client.get("/api/v1/accounts/current/members", headers=SEM_CONTA)
        assert r.status_code == 400

    def test_an_insufficient_role_gives_a_403(self, client, core):
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

    def test_with_no_token_it_gives_a_401(self, client):
        assert client.get("/api/v1/accounts").status_code == 401


class TestListings:
    def test_accounts_translates_the_proto(self, client):
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

    def test_members_translates_the_role_into_a_string(self, client):
        r = client.get("/api/v1/accounts/current/members", headers=ACCOUNT)
        assert r.status_code == 200
        assert r.json() == [{"id": "m-1", "user_id": "u-1", "role": "admin"}]


class TestWrites:
    def test_it_creates_an_organization_with_an_idempotency_key(self, client, core):
        r = client.post(
            "/api/v1/accounts",
            headers=ACCOUNT,
            json={"handle": "acme", "display_name": "ACME", "legal_id": "00.000.000/0001-00"},
        )
        assert r.status_code == 201
        req = core.CreateAccount.last["request"]
        assert req.kind == identity_pb2.Account.KIND_ORGANIZATION
        assert req.handle == "acme"
        assert req.idempotency_key  # repeating must not duplicate (ADR-0017)

    def test_creating_an_organization_requires_an_active_account(self, client):
        r = client.post(
            "/api/v1/accounts", headers=SEM_CONTA, json={"handle": "a", "display_name": "A"}
        )
        assert r.status_code == 400

    def test_it_creates_an_invite_with_a_role_and_grants(self, client, core):
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

    def test_an_invite_requires_a_management_role(self, monkeypatch, core):
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
