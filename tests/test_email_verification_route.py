"""Proving the address, before there is anybody to prove it for.

This route is the one place in the edge where the person is authenticated and
the core still does not know them: EnsureUser refuses an unverified password
credential before creating anything (spec SP-0 D-5), which is the very 412 this
request exists to lift.
"""

import pytest

from app.usecases import identity as uc
from tests.conftest import token_for


class _FakeAdmin:
    def __init__(self, link="https://dop-t.com/verify?oob=abc"):
        self.link = link
        self.asked = []

    async def verification_link(self, email, continue_url=""):
        self.asked.append(email)
        return self.link


@pytest.fixture
def admin(monkeypatch):
    fake = _FakeAdmin()
    monkeypatch.setattr(uc, "firebase_admin", lambda: fake)
    return fake


def _headers(**kw):
    return {"authorization": token_for(email_verified=False, **kw)}


def test_it_generates_the_link_and_asks_the_core_to_send_it(client, core, admin):
    r = client.post("/api/v1/verification/email", headers=_headers())

    assert r.status_code == 200
    assert r.json() == {"email": "dev@dop.local"}
    assert admin.asked == ["dev@dop.local"]
    sent = core.SendEmailVerification.requests[-1]
    assert sent.email == "dev@dop.local"
    assert sent.link == "https://dop-t.com/verify?oob=abc"
    assert sent.subject == "sub-1"


def test_the_core_is_never_asked_to_resolve_the_user(client, core, admin):
    """The whole reason this route is @token_only.

    Resolving would call EnsureUser, which refuses this exact credential — the
    request would answer 412 to the person asking for the thing that lifts it.
    """
    client.post("/api/v1/verification/email", headers=_headers())

    assert core.EnsureUser.calls == [], (
        "the middleware resolved the user: this route would 412 forever"
    )


def test_the_address_comes_from_the_token_and_not_from_a_body(client, core, admin):
    """A body naming somebody else's address must change nothing.

    If it could, anybody holding any valid token could post a DOP-branded
    message to an address of their choosing — a phishing kit with our logo.
    """
    client.post(
        "/api/v1/verification/email",
        headers=_headers(),
        json={"email": "victim@example.com"},
    )

    assert admin.asked == ["dev@dop.local"]
    assert core.SendEmailVerification.requests[-1].email == "dev@dop.local"


def test_asking_for_an_already_verified_address_sends_nothing(client, core, admin):
    """Clicking twice is not an error, and telling somebody their finished
    account failed would be a worse answer than doing nothing."""
    r = client.post(
        "/api/v1/verification/email",
        headers={"authorization": token_for(email_verified=True)},
    )

    assert r.status_code == 200
    assert admin.asked == []
    assert core.SendEmailVerification.calls == []


def test_a_provider_that_cannot_mint_the_link_is_a_503_and_sends_nothing(
    client, core, monkeypatch
):
    from app.platform.security.firebase_admin import LinkNotGenerated

    class _Broken:
        async def verification_link(self, email, continue_url=""):
            raise LinkNotGenerated("HTTP 400: EMAIL_NOT_FOUND")

    monkeypatch.setattr(uc, "firebase_admin", lambda: _Broken())

    r = client.post("/api/v1/verification/email", headers=_headers())

    assert r.status_code == 503
    # The message must not go out with a dead link in it — an e-mail that looks
    # right, arrives, and cannot be acted on is the failure nobody reports.
    assert core.SendEmailVerification.calls == []
