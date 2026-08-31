"""Hierarquia nos dois transportes, contra um núcleo falso.

O teste que mais importa aqui é o de paridade: ele é o que impede alguém de
reimplementar um caso de uso no servicer (ou no router) sem ninguém notar na
revisão.
"""

import pytest

from app.grpcapi.gen.dop.bff.v1 import hierarchy_pb2 as bff
from tests.conftest import metadados_de, token_de

CONTA = metadados_de(token_de())
CABECALHOS_REST = {"authorization": token_de(), "x-account-id": "acct-1"}


class TestREST:
    def test_tree_traz_workspaces_com_projetos(self, cliente_hier):
        r = cliente_hier.get("/api/v1/tree", headers=CABECALHOS_REST)
        assert r.status_code == 200
        nos = r.json()
        assert len(nos) == 1
        assert nos[0]["workspace"]["key"] == "PLAT"
        assert [p["name"] for p in nos[0]["projects"]] == ["Cockpit", "Sem quadro"]

    def test_projeto_sem_quadro_vem_nulo_nao_zerado(self, cliente_hier):
        """Ausente e zerado são coisas diferentes.

        Um vínculo zerado faria o cockpit desenhar 'quadro configurado' com
        campos vazios, quando a verdade é que não há quadro nenhum.
        """
        nos = cliente_hier.get("/api/v1/tree", headers=CABECALHOS_REST).json()
        com, sem = nos[0]["projects"]
        assert com["task_manager"]["external_space_id"] == "sp-901"
        assert sem["task_manager"] is None

    def test_criar_workspace_exige_papel(self, cliente_hier, nucleo):
        """Developer não reorganiza a conta."""
        nucleo.papel_developer()
        r = cliente_hier.post(
            "/api/v1/workspaces",
            headers=CABECALHOS_REST,
            json={"name": "Nova", "key": "NOVA"},
        )
        assert r.status_code == 403

    def test_criar_workspace(self, cliente_hier, hierarquia):
        r = cliente_hier.post(
            "/api/v1/workspaces",
            headers=CABECALHOS_REST,
            json={"name": "Plataforma", "key": "PLAT"},
        )
        assert r.status_code == 201
        assert hierarquia.CreateWorkspace.pedidos[0].key == "PLAT"

    def test_escrita_carrega_idempotencia(self, cliente_hier, hierarquia):
        """Sem chave de idempotência, o retry do canal duplica workspace."""
        cliente_hier.post(
            "/api/v1/workspaces",
            headers=CABECALHOS_REST,
            json={"name": "Plataforma", "key": "PLAT"},
        )
        assert hierarquia.CreateWorkspace.pedidos[0].idempotency_key != ""

    def test_sem_conta_ativa_e_recusado(self, cliente_hier):
        r = cliente_hier.get("/api/v1/tree", headers={"authorization": token_de()})
        assert r.status_code == 400


class TestGRPC:
    async def test_get_tree(self, stub_hier):
        resp = await stub_hier.GetTree(bff.GetTreeRequest(), metadata=CONTA)
        assert len(resp.nodes) == 1
        assert resp.nodes[0].workspace.key == "PLAT"

    async def test_projeto_sem_quadro_nao_tem_campo(self, stub_hier):
        resp = await stub_hier.GetTree(bff.GetTreeRequest(), metadata=CONTA)
        com, sem = resp.nodes[0].projects
        assert com.HasField("task_manager")
        assert not sem.HasField("task_manager")

    async def test_sem_token_e_unauthenticated(self, stub_hier):
        import grpc

        with pytest.raises(grpc.aio.AioRpcError) as e:
            await stub_hier.GetTree(bff.GetTreeRequest(), metadata=metadados_de(None))
        assert e.value.code() == grpc.StatusCode.UNAUTHENTICATED

    async def test_criar_workspace_exige_papel(self, stub_hier, nucleo):
        import grpc

        nucleo.papel_developer()
        with pytest.raises(grpc.aio.AioRpcError) as e:
            await stub_hier.CreateWorkspace(
                bff.CreateWorkspaceRequest(name="Nova", key="NOVA"), metadata=CONTA
            )
        assert e.value.code() == grpc.StatusCode.PERMISSION_DENIED

    async def test_cliente_pode_mandar_a_propria_idempotencia(self, stub_hier, hierarquia):
        """No gRPC o cliente sabe se está retentando — e por isso pode mandar
        a chave dele. O REST não tem onde carregá-la e recebe uma nossa."""
        await stub_hier.CreateWorkspace(
            bff.CreateWorkspaceRequest(name="P", key="P", idempotency_key="minha-chave"),
            metadata=CONTA,
        )
        assert hierarquia.CreateWorkspace.pedidos[0].idempotency_key == "minha-chave"

    async def test_bind_task_manager_preserva_o_resto_do_projeto(
        self, stub_hier, hierarquia
    ):
        """UpdateProject no núcleo é SUBSTITUIÇÃO.

        Se a borda mandasse só o vínculo, nome, descrição e regras do projeto
        seriam apagados. Este teste é o que impede essa regressão silenciosa.
        """
        await stub_hier.BindTaskManager(
            bff.BindTaskManagerRequest(
                project_id="prj-1",
                binding=bff.TaskManagerBinding(
                    integration_id="res-9", external_space_id="sp-1"
                ),
            ),
            metadata=CONTA,
        )
        enviado = hierarquia.UpdateProject.pedidos[0].project
        assert enviado.name == "Cockpit"
        assert list(enviado.rules) == ["no-green-no-pr"]
        assert enviado.task_manager.integration.id == "res-9"


class TestParidadeEntreTransportes:
    """Uma função, dois adaptadores — e a prova de que continua assim."""

    async def test_tree_igual_nas_duas_portas(self, cliente_hier, stub_hier):
        rest = cliente_hier.get("/api/v1/tree", headers=CABECALHOS_REST).json()
        grpc_resp = await stub_hier.GetTree(bff.GetTreeRequest(), metadata=CONTA)

        assert len(grpc_resp.nodes) == len(rest)
        for no_grpc, no_rest in zip(grpc_resp.nodes, rest, strict=True):
            assert no_grpc.workspace.id == no_rest["workspace"]["id"]
            assert no_grpc.workspace.key == no_rest["workspace"]["key"]
            assert [p.name for p in no_grpc.projects] == [
                p["name"] for p in no_rest["projects"]
            ]
            # E o vínculo, que é onde ausente≠zerado pode divergir entre pontas.
            for p_grpc, p_rest in zip(no_grpc.projects, no_rest["projects"], strict=True):
                assert p_grpc.HasField("task_manager") == (
                    p_rest["task_manager"] is not None
                )
