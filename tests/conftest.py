"""The core's doubles. No test brings up a real dop-core.

There is a SINGLE substitution point: `app.coreclient.stubs.identity_stub`. The
router, the gRPC servicer and the resolver all go through it, so swapping that
function swaps the whole core — with no patching scattered across modules.

The gRPC port's fixtures (`grpc_server`, `stub_grpc`) bring up a real server on
an ephemeral port, against the same fake core as the REST fixtures. It is what
allows asking for the same thing through both ports and comparing the result.
"""

import base64
import json
import time

import grpc
import pytest
from fastapi.testclient import TestClient
from google.protobuf import struct_pb2
from grpc.aio import AioRpcError

from app.coreclient import stubs
from app.coreclient.gen.dop.v1 import (
    common_pb2,
    hierarchy_pb2,
    identity_pb2,
    resource_pb2,
)
from app.coreclient.resolver import CoreResolver
from app.grpcapi.gen.dop.bff.v1 import hierarchy_pb2_grpc as bff_hier_grpc
from app.grpcapi.gen.dop.bff.v1 import identity_pb2_grpc as bff_grpc
from app.grpcapi.gen.dop.bff.v1 import resource_pb2_grpc as bff_res_grpc
from app.grpcapi.server import GrpcServer
from app.main import create_app
from app.platform.security.firebase import FirebaseVerifier
from app.settings import settings

PROJECT = "dop-local"


def token_for(subject="sub-1", email="dev@dop.local", name="Dev"):
    """The emulator's token: it is not signed, but it normalizes just like production's."""
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
    return f"Bearer header.{raw}.signature"


def grpc_error(code: grpc.StatusCode, details="came from the core") -> AioRpcError:
    return AioRpcError(code, grpc.aio.Metadata(), grpc.aio.Metadata(), details)


class FakeCall:
    """One RPC. It keeps what it received; it returns what it was told to return."""

    def __init__(self, result=None):
        self.result = result
        self.calls: list[dict] = []

    def returns(self, result):
        self.result = result
        return self

    def fails_with(self, code: grpc.StatusCode, details="came from the core"):
        self.result = grpc_error(code, details)
        return self

    @property
    def last(self) -> dict:
        assert self.calls, "the RPC was not called"
        return self.calls[-1]

    def metadata(self) -> dict[str, str]:
        return dict(self.last["metadata"])

    @property
    def requests(self):
        """Only the messages, without the rest — what most tests want."""
        return [c["request"] for c in self.calls]

    async def __call__(self, request, *, metadata=None, timeout=None, **_):
        self.calls.append({"request": request, "metadata": metadata, "timeout": timeout})
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class FakeCore:
    """A stub of the IdentityService with the RPCs the edge uses."""

    def __init__(self, user_id="u-1", account_id="acct-1", role=identity_pb2.ROLE_ADMIN):
        self.user_id = user_id
        account = identity_pb2.Account(
            id=account_id,
            kind=identity_pb2.Account.KIND_ORGANIZATION,
            handle="acme",
            display_name="ACME",
        )
        self.EnsureUser = FakeCall(identity_pb2.User(id=user_id, email="dev@dop.local"))
        self.ListAccounts = FakeCall(
            identity_pb2.ListAccountsResponse(accounts=[account])
        )
        self.ListMemberships = FakeCall(
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
        self.account = account
        self.CreateAccount = FakeCall(account)
        self.CreateInvite = FakeCall(
            identity_pb2.Invite(
                id="inv-1",
                email="novo@dop.local",
                role=identity_pb2.ROLE_DEVELOPER,
                status=identity_pb2.Invite.STATUS_PENDING,
            )
        )


    def demote_to_developer(self):
        """Demotes the actor to developer.

        The role is resolved ONCE, at login, from ListMemberships — so changing
        the role means changing what that RPC answers, not a shortcut in the
        context. Testing through the shortcut would prove less than it seems.
        """
        self.ListMemberships = FakeCall(
            identity_pb2.ListMembershipsResponse(
                memberships=[
                    identity_pb2.Membership(
                        id="m-1",
                        user=common_pb2.UserRef(id=self.user_id),
                        account=common_pb2.AccountRef(id=self.account.id),
                        role=identity_pb2.ROLE_DEVELOPER,
                    )
                ]
            )
        )


@pytest.fixture
def core(monkeypatch):
    monkeypatch.setenv("FIREBASE_AUTH_EMULATOR_HOST", "localhost:9099")
    fake = FakeCore()
    monkeypatch.setattr(stubs, "identity_stub", lambda: fake)
    return fake


@pytest.fixture
def client(core):
    with TestClient(create_app()) as c:
        yield c


@pytest.fixture(autouse=True)
def _grpc_port_disabled(monkeypatch):
    """No test opens the gRPC port through the lifespan.

    Every `create_app()` in a test would try to listen on the SAME fixed port
    from the configuration — the second would fail, and the suite would become
    order-dependent. Whoever tests the gRPC port creates it explicitly, on an
    ephemeral port.
    """
    monkeypatch.setattr(settings, "grpc_enabled", False)


def metadata_for(token: str | None = None, account_id: str = "acct-1", **extra):
    """gRPC metadata equivalent to REST's headers.

    Identity in `authorization` (not in the message's body) and the active
    account in `x-account-id`, exactly as AuthMiddleware expects over HTTP.
    """
    md = []
    if token is not None:
        md.append(("authorization", token))
    if account_id:
        md.append(("x-account-id", account_id))
    md += list(extra.items())
    return md


@pytest.fixture
async def grpc_server(core):
    """A real gRPC server, on an ephemeral port, against the fake core.

    Real on purpose: an interceptor exercised only by a direct call proves
    neither that `grpc.aio` runs it in the right order nor that the ContextVar
    survives as far as the servicer — which is precisely the hard part.
    """
    server = GrpcServer(
        verifier=FirebaseVerifier(PROJECT),
        resolver=CoreResolver(),
        port=0,
        host="127.0.0.1",
    )
    await server.start()
    try:
        yield server
    finally:
        await server.stop(grace=0)


@pytest.fixture
async def stub_grpc(grpc_server):
    async with grpc.aio.insecure_channel(f"127.0.0.1:{grpc_server.port}") as channel:
        yield bff_grpc.IdentityServiceStub(channel)


# ── hierarchy ───────────────────────────────────────────────────────────────


class FakeHierarchy:
    """The hierarchy's fake core. The same discipline as FakeCore: it records
    the request and the metadata that arrived, so the parity test can prove that
    REST and gRPC build the SAME request."""

    def __init__(self):
        ws = hierarchy_pb2.Workspace(
            id="ws-1",
            account=common_pb2.AccountRef(id="acct-1"),
            name="Platform",
            key="PLAT",
            description="a test workspace",
            tags=["active"],
        )
        prj = hierarchy_pb2.Project(
            id="prj-1",
            workspace=common_pb2.WorkspaceRef(id="ws-1"),
            name="Cockpit",
            description="a test project",
            rules=["no-green-no-pr"],
        )
        prj.task_manager.CopyFrom(
            hierarchy_pb2.ProjectTaskManager(
                integration=common_pb2.ResourceRef(id="res-1"),
                external_space_id="sp-901",
                external_project_id="pj-42",
                card_types=["bug", "feature"],
            )
        )
        # A project with NO board: it is the case that tells absent from zeroed.
        without_board = hierarchy_pb2.Project(
            id="prj-2",
            workspace=common_pb2.WorkspaceRef(id="ws-1"),
            name="No board",
        )
        self.workspace, self.project, self.project_without_board = ws, prj, without_board

        self.GetTree = FakeCall(
            hierarchy_pb2.GetTreeResponse(
                nodes=[
                    hierarchy_pb2.GetTreeResponse.Node(
                        workspace=ws, projects=[prj, without_board]
                    )
                ]
            )
        )
        self.ListWorkspaces = FakeCall(
            hierarchy_pb2.ListWorkspacesResponse(workspaces=[ws])
        )
        self.CreateWorkspace = FakeCall(ws)
        self.ListProjects = FakeCall(
            hierarchy_pb2.ListProjectsResponse(projects=[prj, without_board])
        )
        self.GetProject = FakeCall(prj)
        self.CreateProject = FakeCall(prj)
        self.UpdateProject = FakeCall(prj)


@pytest.fixture
def hierarchy(core, monkeypatch):
    fake = FakeHierarchy()
    monkeypatch.setattr(stubs, "hierarchy_stub", lambda: fake)
    return fake


@pytest.fixture
def client_hier(hierarchy):
    with TestClient(create_app()) as c:
        yield c


@pytest.fixture
async def stub_hier(grpc_server, hierarchy):
    async with grpc.aio.insecure_channel(f"127.0.0.1:{grpc_server.port}") as channel:
        yield bff_hier_grpc.HierarchyServiceStub(channel)


# ── resources ───────────────────────────────────────────────────────────────


class FakeResources:
    """The resources' fake core.

    The resource it returns has `credential_ref` filled in and NO field with the
    value — just like the real core. A double that returned the secret would make
    the leak test pass by accident.
    """

    def __init__(self):
        config = struct_pb2.Struct()
        config.update({"category": "task_manager", "provider": "clickup"})
        r = resource_pb2.Resource(
            id="res-1",
            account=common_pb2.AccountRef(id="acct-1"),
            kind=resource_pb2.Resource.KIND_INTEGRATION,
            name="The team's ClickUp",
            version=1,
            config=config,
            credential_ref="integration_credential:acct-1:res-1",
        )
        self.resource = r
        self.ListResources = FakeCall(
            resource_pb2.ListResourcesResponse(resources=[r])
        )
        self.GetResource = FakeCall(r)
        self.CreateResource = FakeCall(r)
        self.SetCredential = FakeCall(
            resource_pb2.SetCredentialResponse(credential_ref=r.credential_ref)
        )
        self.GrantResource = FakeCall(
            resource_pb2.ResourceGrant(
                id="g-1",
                resource=common_pb2.ResourceRef(id="res-1"),
                user=common_pb2.UserRef(id="u-2"),
                level="use",
            )
        )
        self.RevokeGrant = FakeCall(resource_pb2.RevokeGrantResponse(revoked=True))


@pytest.fixture
def resources(core, monkeypatch):
    fake = FakeResources()
    monkeypatch.setattr(stubs, "resource_stub", lambda: fake)
    return fake


@pytest.fixture
def client_res(resources):
    with TestClient(create_app()) as c:
        yield c


@pytest.fixture
async def stub_res(grpc_server, resources):
    async with grpc.aio.insecure_channel(f"127.0.0.1:{grpc_server.port}") as channel:
        yield bff_res_grpc.ResourceServiceStub(channel)
