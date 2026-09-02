"""The invite's path (P-32).

`AcceptInvite` existed in the core and nothing reached it: the e-mail went out
with a link to a 404. What is tested here is the path being closed, and the two
properties that make the link safe to send (ADR-0026):

  - the preview does NOT say who the invite was for;
  - reading it requires a session, but NOT an active account.
"""

import grpc
import pytest
from fastapi.testclient import TestClient
from google.protobuf import timestamp_pb2

from app.coreclient.gen.dop.v1 import common_pb2, identity_pb2
from app.main import create_app
from tests.conftest import FakeCall, token_for

ACCOUNT = {"authorization": token_for(), "x-account-id": "acct-1"}
NO_ACCOUNT = {"authorization": token_for()}

INVITEE = "invitee@acme.test"


@pytest.fixture
def invites(core):
    """Adds the invite RPCs to the fake core."""
    expires = timestamp_pb2.Timestamp()
    expires.FromSeconds(1_757_000_000)

    core.ListInvites = FakeCall(
        identity_pb2.ListInvitesResponse(
            invites=[
                identity_pb2.Invite(
                    id="inv-1",
                    email=INVITEE,
                    role=identity_pb2.ROLE_DEVELOPER,
                    status=identity_pb2.Invite.STATUS_PENDING,
                    expires_at=expires,
                ),
                identity_pb2.Invite(
                    id="inv-0",
                    email="old@acme.test",
                    role=identity_pb2.ROLE_VIEWER,
                    status=identity_pb2.Invite.STATUS_REVOKED,
                ),
            ]
        )
    )
    core.GetInvite = FakeCall(
        identity_pb2.InvitePreview(
            id="inv-1",
            account_name="ACME",
            role=identity_pb2.ROLE_DEVELOPER,
            status=identity_pb2.Invite.STATUS_PENDING,
            expires_at=expires,
            usable=True,
        )
    )
    core.AcceptInvite = FakeCall(
        identity_pb2.Membership(
            id="mem-9",
            user=common_pb2.UserRef(id="u-1"),
            account=common_pb2.AccountRef(id="acct-1"),
            role=identity_pb2.ROLE_DEVELOPER,
        )
    )
    core.GetAccount = FakeCall(core.account)
    core.RevokeInvite = FakeCall(
        identity_pb2.Invite(
            id="inv-1",
            email=INVITEE,
            role=identity_pb2.ROLE_DEVELOPER,
            status=identity_pb2.Invite.STATUS_REVOKED,
        )
    )
    core.UpdateMembership = FakeCall(
        identity_pb2.Membership(
            id="mem-2",
            user=common_pb2.UserRef(id="u-2"),
            account=common_pb2.AccountRef(id="acct-1"),
            role=identity_pb2.ROLE_ADMIN,
        )
    )
    core.RemoveMembership = FakeCall(identity_pb2.RemoveMembershipResponse(removed=True))
    return core


@pytest.fixture
def client_inv(invites):
    with TestClient(create_app()) as c:
        yield c


class TestThePreviewIsSafeToOpen:
    def test_it_does_not_say_who_the_invite_was_for(self, client_inv):
        """Whoever finds the link must not learn an address from it — that would
        turn it back into the oracle taking the token out was meant to end."""
        r = client_inv.get("/api/v1/invites/inv-1", headers=NO_ACCOUNT)
        assert r.status_code == 200
        assert INVITEE not in r.text
        assert r.json()["account_name"] == "ACME"
        assert r.json()["usable"] is True

    def test_it_does_not_require_an_active_account(self, client_inv):
        """Whoever opens an invite may not be a member of anything yet — that is
        the point of an invite. Requiring an account here would be asking
        somebody to already be inside in order to be let in."""
        r = client_inv.get("/api/v1/invites/inv-1", headers=NO_ACCOUNT)
        assert r.status_code == 200

    def test_with_no_token_it_is_a_401(self, client_inv):
        """It requires a SESSION all the same: without one, an id found in a log
        would tell a stranger that an account named X invited somebody as an
        admin."""
        r = client_inv.get("/api/v1/invites/inv-1")
        assert r.status_code == 401


class TestAccepting:
    def test_it_returns_the_account_just_joined(self, client_inv):
        """So the cockpit can switch to it with no second round trip: whoever
        accepts an invite wants to be inside."""
        r = client_inv.post("/api/v1/invites/inv-1/accept", headers=ACCOUNT)
        assert r.status_code == 200
        assert r.json()["account_id"] == "acct-1"
        assert r.json()["role"] == "developer"

    def test_it_carries_an_idempotency_key(self, client_inv, invites):
        """Accepting twice must not create two memberships — the channel's retry
        is the common case."""
        client_inv.post("/api/v1/invites/inv-1/accept", headers=ACCOUNT)
        assert invites.AcceptInvite.requests[0].idempotency_key != ""

    def test_the_two_refusals_come_back_distinguishable(self, client_inv, invites):
        """'Confirm your e-mail' and 'this invite is not yours' send the person to
        do different things — and the cockpit shows different texts (ADR-0026)."""
        invites.AcceptInvite.fails_with(
            grpc.StatusCode.FAILED_PRECONDITION, "acceptance requires a verified email"
        )
        unverified = client_inv.post("/api/v1/invites/inv-1/accept", headers=ACCOUNT)
        assert unverified.status_code == 412

        invites.AcceptInvite.fails_with(
            grpc.StatusCode.PERMISSION_DENIED, "this invite was issued to a different email"
        )
        wrong = client_inv.post("/api/v1/invites/inv-1/accept", headers=ACCOUNT)
        assert wrong.status_code == 403
        # And the refusal does not reveal the address either.
        assert INVITEE not in wrong.text


class TestTheList:
    def test_it_brings_the_history_and_not_only_the_pending_ones(self, client_inv):
        """'What happened to the invite I sent yesterday?' is answered by the
        revoked row."""
        body = client_inv.get("/api/v1/invites", headers=ACCOUNT).json()
        assert [i["status"] for i in body] == ["pending", "revoked"]

    def test_a_developer_does_not_see_the_list(self, client_inv, invites):
        """It carries the addresses of people who were invited, and that is not
        public inside the account."""
        invites.demote_to_developer()
        assert client_inv.get("/api/v1/invites", headers=ACCOUNT).status_code == 403

    def test_a_developer_does_not_revoke_or_change_a_role(self, client_inv, invites):
        invites.demote_to_developer()
        assert client_inv.delete("/api/v1/invites/inv-1", headers=ACCOUNT).status_code == 403
        assert (
            client_inv.patch(
                "/api/v1/members/mem-2", headers=ACCOUNT, json={"role": "admin"}
            ).status_code
            == 403
        )

    def test_an_expiry_that_was_never_set_is_null(self, client_inv):
        body = client_inv.get("/api/v1/invites", headers=ACCOUNT).json()
        assert body[0]["expires_at"] is not None
        assert body[1]["expires_at"] is None


class TestRemovingAMember:
    def test_it_removes_and_answers_with_no_body(self, client_inv, invites):
        r = client_inv.delete("/api/v1/members/mem-2", headers=ACCOUNT)
        assert r.status_code == 204
        assert invites.RemoveMembership.last["request"].membership_id == "mem-2"

    def test_the_core_refusal_reaches_the_screen(self, client_inv, invites):
        """The last owner, a personal account, a developer trying: all of them are
        the CORE's rules. The edge repeats none of them — it carries the refusal."""
        invites.RemoveMembership.fails_with(
            grpc.StatusCode.FAILED_PRECONDITION, "the account needs at least one owner"
        )
        r = client_inv.delete("/api/v1/members/mem-2", headers=ACCOUNT)
        assert r.status_code == 412
        assert "owner" in r.json()["detail"]


class TestAMembersGrants:
    def test_it_lists_the_grants_of_one_member(self, client, resources):
        r = client.get("/api/v1/members/u-2/grants", headers=ACCOUNT)
        assert r.status_code == 200
        assert r.json() == [
            {"id": "g-1", "resource_id": "res-1", "user_id": "u-2", "level": "use"}
        ]
        assert resources.ListMemberGrants.last["request"].user_id == "u-2"
