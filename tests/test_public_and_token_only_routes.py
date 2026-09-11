"""The route markers actually register.

This file exists because they did not. `@public` swept `app.routes` looking for
endpoints, and this FastAPI version does not put them there — `include_router`
leaves a wrapper holding its children under `original_router`. The sweep found
nothing, every marker registered nothing, and it went unnoticed for one reason:
it fails CLOSED. A @public route merely goes on demanding a token, and the only
route anybody would have tested — /healthz — kept working because the middleware
carries a hardcoded list of paths as well.

So the assertion here is not about a behaviour, it is about the wiring: after
startup, the patterns are not empty.
"""

from fastapi.testclient import TestClient

from app.main import create_app
from app.platform.security import decorator as d


def test_the_public_marker_reaches_the_registry():
    with TestClient(create_app()):
        patterns = [(m, p.pattern) for m, p in d._PUBLIC_PATTERNS]

    assert patterns, (
        "no @public route registered: the sweep is not finding the endpoints, "
        "and every marker in this module is silently doing nothing"
    )
    assert ("GET", "^/healthz$") in patterns


def test_the_token_only_marker_reaches_the_registry():
    with TestClient(create_app()):
        patterns = [(m, p.pattern) for m, p in d._TOKEN_ONLY_PATTERNS]

    assert ("POST", "^/api/v1/verification/email$") in patterns, (
        "the verification route is not marked token_only: the middleware would "
        "ask the core to resolve a user who cannot exist yet, and the request "
        "that exists to lift the 412 would itself be answered with one"
    )
