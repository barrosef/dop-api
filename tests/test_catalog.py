"""The catalogues the journey reads: a session, no account, the rows as the
core seeds them — features as a list, `operated` as the honest flag."""

from app.grpcapi.gen.dop.bff.v1 import catalog_pb2 as bff
from app.grpcapi.gen.dop.bff.v1 import catalog_pb2_grpc as bff_grpc
from tests.conftest import metadata_for, token_for

SESSION = {"authorization": token_for()}


def test_plans_carry_features_and_no_price(client, catalog):
    r = client.get("/api/v1/catalog/plans", headers=SESSION)
    assert r.status_code == 200
    assert r.json() == [
        {"key": "free", "name": "Free", "tagline": "Try it.", "features": ["1 workspace"]},
        {"key": "pro", "name": "Pro", "tagline": "Teams.", "features": []},
    ]
    assert "price" not in r.json()[0]


def test_providers_carry_the_operated_flag(client, catalog):
    r = client.get("/api/v1/catalog/providers", headers=SESSION)
    assert r.status_code == 200
    by_key = {p["key"]: p for p in r.json()}
    assert by_key["github"]["operated"] is True
    assert by_key["github"]["permissions"] == ["repo"]
    assert by_key["jira"]["operated"] is False
    assert by_key["jira"]["needs_base_url"] is True
    assert by_key["jira"]["category"] == "task_manager"


def test_the_catalog_needs_a_session(client, catalog):
    assert client.get("/api/v1/catalog/plans").status_code == 401


async def test_parity_between_transports(client, grpc_server, catalog):
    import grpc

    rest = client.get("/api/v1/catalog/providers", headers=SESSION).json()
    async with grpc.aio.insecure_channel(f"127.0.0.1:{grpc_server.port}") as channel:
        stub = bff_grpc.CatalogServiceStub(channel)
        resp = await stub.ListProviders(
            bff.ListProvidersRequest(), metadata=metadata_for(token_for(), account_id="")
        )
    assert [(p.key, p.operated, list(p.permissions)) for p in resp.providers] == [
        (p["key"], p["operated"], p["permissions"]) for p in rest
    ]
