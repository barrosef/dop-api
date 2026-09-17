"""An end-to-end check of the edge: middlewares, decorators and JSON logging."""

import json

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from tests.conftest import token_for


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


class TestTheProofSentToTheCore:
    """ADR-0022 — the edge proves who is calling instead of asserting it."""

    VECTOR = (
        "YmZmfHVzci0xfHVzZXJ8YWNjdC05fHNlc3MtM3wxODAwMDAwMDYw"
        ".54240927df959a745d2ba1a1fc1947a9e9466f32be02b6b96876ebe930aed530"
    )
    KEY = "a-key-long-enough-for-a-test-0123"

    def test_the_wire_format_is_pinned_to_the_same_bytes_as_the_core(self):
        """The core has this SAME vector in a Go test.

        The two implementations are in different languages and can only agree by
        accident unless something pins them. A drift here does not fail loudly —
        it makes every call arrive unauthenticated, which in strict mode is an
        outage and in permissive mode is silence.
        """
        from app.coreclient import callauth

        got = callauth.sign(
            self.KEY,
            caller="bff",
            actor_id="usr-1",
            actor_kind="user",
            account_id="acct-9",
            session_id="sess-3",
            now=1_800_000_000 - callauth.TTL_SECONDS + 60,
        )
        assert got == self.VECTOR

    def test_an_id_with_a_separator_is_refused_rather_than_escaped(self):
        from app.coreclient import callauth

        with pytest.raises(ValueError):
            callauth.sign(self.KEY, actor_id="usr|1")

    def test_the_call_carries_the_assertion_and_the_token(self, client, core, monkeypatch):
        headers = {"authorization": token_for(), "x-account-id": "acct-1"}
        """What the core needs to verify has to actually leave here."""
        from app.settings import settings

        monkeypatch.setattr(settings, "call_auth_key", self.KEY)
        client.get("/api/v1/accounts", headers=headers)
        md = core.ListAccounts.metadata()
        assert md["x-dop-assertion"], "the assertion did not go out"
        assert md["authorization"].startswith("Bearer "), "the person's token did not go out"

    def test_with_no_key_the_edge_signs_nothing(self, client, core, monkeypatch):
        headers = {"authorization": token_for(), "x-account-id": "acct-1"}
        """The migration needs this: an edge that cannot sign yet still works
        against a core running in permissive."""
        from app.settings import settings

        monkeypatch.setattr(settings, "call_auth_key", "")
        client.get("/api/v1/accounts", headers=headers)
        assert "x-dop-assertion" not in core.ListAccounts.metadata()
