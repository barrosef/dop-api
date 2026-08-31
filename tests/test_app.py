"""Verificação de ponta a ponta da borda: middlewares, decorators e log JSON."""

import json

from fastapi.testclient import TestClient

from app.main import create_app


def test_healthz_e_publico_sem_token():
    """@public isenta de autenticação — sem ele, a sonda do k8s falharia."""
    with TestClient(create_app()) as client:
        r = client.get("/healthz")
        assert r.status_code == 200
        assert r.json() == {"status": "ok"}


def test_rota_protegida_recusa_sem_token():
    with TestClient(create_app()) as client:
        r = client.get("/api/v1/me")
        assert r.status_code == 401


def test_request_id_volta_no_cabecalho():
    """Rastro entre serviços: o mesmo id aparece no log do BFF e do core."""
    with TestClient(create_app()) as client:
        r = client.get("/healthz", headers={"x-request-id": "trace-abc-123"})
        assert r.headers["x-request-id"] == "trace-abc-123"


def test_request_id_e_gerado_quando_ausente():
    with TestClient(create_app()) as client:
        r = client.get("/healthz")
        assert len(r.headers.get("x-request-id", "")) == 32


def test_log_sai_em_json_valido(capsys):
    """As duas pontas escrevem JSON com os mesmos campos canônicos."""
    with TestClient(create_app()) as client:
        client.get("/healthz")
    linhas = [
        json.loads(line)
        for line in capsys.readouterr().out.splitlines()
        if line.startswith("{")
    ]
    assert linhas, "nenhuma linha JSON foi emitida"
    req = [line for line in linhas if line.get("event") == "request"]
    assert req, "faltou a linha de request"
    entry = req[0]
    for campo in ("ts", "level", "component", "request_id", "duration_ms", "status"):
        assert campo in entry, f"campo canônico ausente: {campo}"
    assert entry["component"] == "dop-api"
