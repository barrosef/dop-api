"""Hierarchy on both transports, against a fake core.

The test that matters most here is the parity one: it is what stops anybody
from reimplementing a use case in the servicer (or in the router) with nobody
noticing in review.
"""

import pytest

from app.grpcapi.gen.dop.bff.v1 import hierarchy_pb2 as bff
from tests.conftest import metadata_for, token_for

ACCOUNT = metadata_for(token_for())
REST_HEADERS = {"authorization": token_for(), "x-account-id": "acct-1"}


class TestREST:
    def test_the_tree_brings_workspaces_with_projects(self, client_hier):
        r = client_hier.get("/api/v1/tree", headers=REST_HEADERS)
        assert r.status_code == 200
        nos = r.json()
        assert len(nos) == 1
        assert nos[0]["workspace"]["key"] == "PLAT"
        assert [p["name"] for p in nos[0]["projects"]] == ["Cockpit", "No board"]

    def test_a_project_with_no_board_comes_back_null_not_zeroed(self, client_hier):
        """Absent and zeroed are different things.

        A zeroed link would make the cockpit draw 'a board is configured' with
        empty fields, when the truth is that there is no board at all.
        """
        nodes = client_hier.get("/api/v1/tree", headers=REST_HEADERS).json()
        with_board, without_board = nodes[0]["projects"]
        assert with_board["task_manager"]["external_space_id"] == "sp-901"
        assert without_board["task_manager"] is None

    def test_creating_a_workspace_requires_a_role(self, client_hier, core):
        """A developer does not reorganize the account."""
        core.demote_to_developer()
        r = client_hier.post(
            "/api/v1/workspaces",
            headers=REST_HEADERS,
            json={"name": "Nova", "key": "NOVA"},
        )
        assert r.status_code == 403

    def test_creating_a_workspace(self, client_hier, hierarchy):
        r = client_hier.post(
            "/api/v1/workspaces",
            headers=REST_HEADERS,
            json={"name": "Platform", "key": "PLAT"},
        )
        assert r.status_code == 201
        assert hierarchy.CreateWorkspace.requests[0].key == "PLAT"

    def test_a_write_carries_an_idempotency_key(self, client_hier, hierarchy):
        """Sem key de idempotência, o retry do channel duplica workspace."""
        client_hier.post(
            "/api/v1/workspaces",
            headers=REST_HEADERS,
            json={"name": "Platform", "key": "PLAT"},
        )
        assert hierarchy.CreateWorkspace.requests[0].idempotency_key != ""

    def test_with_no_active_account_it_is_refused(self, client_hier):
        r = client_hier.get("/api/v1/tree", headers={"authorization": token_for()})
        assert r.status_code == 400


class TestGRPC:
    async def test_get_tree(self, stub_hier):
        resp = await stub_hier.GetTree(bff.GetTreeRequest(), metadata=ACCOUNT)
        assert len(resp.nodes) == 1
        assert resp.nodes[0].workspace.key == "PLAT"

    async def test_a_project_with_no_board_has_no_field(self, stub_hier):
        resp = await stub_hier.GetTree(bff.GetTreeRequest(), metadata=ACCOUNT)
        with_board, without_board = resp.nodes[0].projects
        assert with_board.HasField("task_manager")
        assert not without_board.HasField("task_manager")

    async def test_with_no_token_it_is_unauthenticated(self, stub_hier):
        import grpc

        with pytest.raises(grpc.aio.AioRpcError) as e:
            await stub_hier.GetTree(bff.GetTreeRequest(), metadata=metadata_for(None))
        assert e.value.code() == grpc.StatusCode.UNAUTHENTICATED

    async def test_creating_a_workspace_requires_a_role(self, stub_hier, core):
        import grpc

        core.demote_to_developer()
        with pytest.raises(grpc.aio.AioRpcError) as e:
            await stub_hier.CreateWorkspace(
                bff.CreateWorkspaceRequest(name="Nova", key="NOVA"), metadata=ACCOUNT
            )
        assert e.value.code() == grpc.StatusCode.PERMISSION_DENIED

    async def test_the_client_may_send_its_own_idempotency_key(self, stub_hier, hierarchy):
        """Over gRPC the client knows whether it is retrying — and that is why
        it may send its own key. REST has nowhere to carry it and gets one of
        ours."""
        await stub_hier.CreateWorkspace(
            bff.CreateWorkspaceRequest(name="P", key="P", idempotency_key="minha-key"),
            metadata=ACCOUNT,
        )
        assert hierarchy.CreateWorkspace.requests[0].idempotency_key == "minha-key"

    async def test_bind_task_manager_preserves_the_rest_of_the_project(
        self, stub_hier, hierarchy
    ):
        """UpdateProject in the core is a REPLACEMENT.

        If the edge sent only the binding, the project's name, description and
        rules would be erased. This test is what stops that silent regression.
        """
        await stub_hier.BindTaskManager(
            bff.BindTaskManagerRequest(
                project_id="prj-1",
                binding=bff.TaskManagerBinding(
                    integration_id="res-9", external_space_id="sp-1"
                ),
            ),
            metadata=ACCOUNT,
        )
        enviado = hierarchy.UpdateProject.requests[0].project
        assert enviado.name == "Cockpit"
        assert list(enviado.rules) == ["no-green-no-pr"]
        assert enviado.task_manager.integration.id == "res-9"


class TestParityBetweenTransports:
    """One function, two adapters — and the proof that it stays that way."""

    async def test_the_tree_is_the_same_on_both_ports(self, client_hier, stub_hier):
        rest = client_hier.get("/api/v1/tree", headers=REST_HEADERS).json()
        grpc_resp = await stub_hier.GetTree(bff.GetTreeRequest(), metadata=ACCOUNT)

        assert len(grpc_resp.nodes) == len(rest)
        for no_grpc, no_rest in zip(grpc_resp.nodes, rest, strict=True):
            assert no_grpc.workspace.id == no_rest["workspace"]["id"]
            assert no_grpc.workspace.key == no_rest["workspace"]["key"]
            assert [p.name for p in no_grpc.projects] == [
                p["name"] for p in no_rest["projects"]
            ]
            # And the binding, which is where absent≠zeroed may diverge between
            # the ends.
            for p_grpc, p_rest in zip(no_grpc.projects, no_rest["projects"], strict=True):
                assert p_grpc.HasField("task_manager") == (
                    p_rest["task_manager"] is not None
                )
