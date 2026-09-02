"""An end-to-end check of the edge: middlewares, decorators and JSON logging."""

import json

from fastapi.testclient import TestClient

from app.main import create_app


def test_healthz_is_public_with_no_token():
    """@public exempts from authentication — without it, k8s' probe would fail."""
    with TestClient(create_app()) as client:
        r = client.get("/healthz")
        assert r.status_code == 200
        assert r.json() == {"status": "ok"}


def test_a_protected_route_refuses_with_no_token():
    with TestClient(create_app()) as client:
        r = client.get("/api/v1/me")
        assert r.status_code == 401


def test_the_request_id_comes_back_in_the_header():
    """A trail between services: the same id appears in the BFF's and the core's log."""
    with TestClient(create_app()) as client:
        r = client.get("/healthz", headers={"x-request-id": "trace-abc-123"})
        assert r.headers["x-request-id"] == "trace-abc-123"


def test_the_request_id_is_generated_when_absent():
    with TestClient(create_app()) as client:
        r = client.get("/healthz")
        assert len(r.headers.get("x-request-id", "")) == 32


def test_the_log_goes_out_as_valid_json(capsys):
    """Both ends write JSON with the same canonical fields."""
    with TestClient(create_app()) as client:
        client.get("/healthz")
    lines = [
        json.loads(line)
        for line in capsys.readouterr().out.splitlines()
        if line.startswith("{")
    ]
    assert lines, "no JSON line was emitted"
    req = [line for line in lines if line.get("event") == "request"]
    assert req, "faltou a linha de request"
    entry = req[0]
    for campo in ("ts", "level", "component", "request_id", "duration_ms", "status"):
        assert campo in entry, f"campo canônico ausente: {campo}"
    assert entry["component"] == "dop-api"
