"""The identity this service presents to the core, and the header it travels in.

The header is the whole point. Cloud Run reads the invoker's token from
`Authorization`, and on the way to the core that header already carries the
PERSON's token (ADR-0029). Sending the service token there would overwrite the
person's, and the failure is silent: every call arrives unauthenticated and
nothing errors at the edge.
"""

import time

import httpx
import pytest

from app.coreclient.client import CoreClient, _ServiceIdentity


class _Recorder:
    """Captures what the gRPC plugin hands back, the way gRPC would."""

    def __init__(self):
        self.metadata = None
        self.error = "unset"

    def __call__(self, metadata, error):
        self.metadata = metadata
        self.error = error


def _identity(monkeypatch, token="tok-1", calls=None):
    def fake_get(url, params=None, headers=None, timeout=None):
        if calls is not None:
            calls.append(params)
        return httpx.Response(200, text=token, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "get", fake_get)
    return _ServiceIdentity("https://dop-core.example/")


def test_the_service_token_does_not_travel_in_authorization(monkeypatch):
    plugin = _identity(monkeypatch)
    recorder = _Recorder()

    plugin(None, recorder)

    keys = {key for key, _ in recorder.metadata}
    assert "x-serverless-authorization" in keys
    # The line that matters: `authorization` belongs to the person.
    assert "authorization" not in keys
    assert dict(recorder.metadata)["x-serverless-authorization"] == "Bearer tok-1"
    assert recorder.error is None


def test_the_token_is_cached_rather_than_minted_per_call(monkeypatch):
    """A metadata round trip per RPC would tax every request in the product."""
    calls = []
    plugin = _identity(monkeypatch, calls=calls)

    for _ in range(3):
        plugin(None, _Recorder())

    assert len(calls) == 1
    assert calls[0]["audience"] == "https://dop-core.example/"


def test_a_token_near_expiry_is_renewed_before_it_dies(monkeypatch):
    """Renewing early rather than racing the expiry — a token that expires mid
    flight fails the call, and the caller cannot tell it from a permission
    problem."""
    plugin = _identity(monkeypatch)
    plugin(None, _Recorder())

    plugin._expires_at = time.time() + 60  # inside the skew window
    plugin(None, _Recorder())

    assert plugin._expires_at > time.time() + 3000


def test_a_metadata_failure_reaches_the_caller_as_an_error(monkeypatch):
    """Never a call with no identity: without the token the core would refuse,
    and the reason has to survive to whoever reads the log."""

    def boom(*args, **kwargs):
        raise httpx.ConnectError("no metadata server here")

    monkeypatch.setattr(httpx, "get", boom)
    recorder = _Recorder()

    _ServiceIdentity("https://dop-core.example/")(None, recorder)

    assert isinstance(recorder.error, Exception)
    assert recorder.metadata == ()


@pytest.mark.parametrize(
    ("audience", "expects_tls"),
    [("", False), ("https://dop-core.example/", True)],
)
def test_the_channel_shape_follows_the_audience(monkeypatch, audience, expects_tls):
    """CORE_AUDIENCE is what says the core is a Cloud Run service. In the local
    cluster it is absent and the channel stays plaintext, which is what the
    in-cluster core speaks."""
    monkeypatch.setenv("CORE_AUDIENCE", audience)
    monkeypatch.setenv("CORE_GRPC", "core.example:9090")

    secure, insecure = [], []
    monkeypatch.setattr(
        "grpc.aio.secure_channel", lambda *a, **k: secure.append(a) or "secure"
    )
    monkeypatch.setattr(
        "grpc.aio.insecure_channel", lambda *a, **k: insecure.append(a) or "insecure"
    )

    client = CoreClient()
    import asyncio

    asyncio.run(client.start())

    assert bool(secure) is expects_tls
    assert bool(insecure) is not expects_tls
