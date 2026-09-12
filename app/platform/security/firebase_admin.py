"""Generating Firebase action links — and only that.

The BFF verifies tokens by hand (`firebase.py`: jwt + httpx, no Admin SDK) and
that stays. What arrives here is the opposite direction: asking Identity
Platform to MINT a verification link, without sending it.

Not sending it is the whole point (spec SP-0 D-7). Firebase's built-in e-mail
would give the platform two mail paths with two appearances and little control
over either; the link comes back to us and the message goes out through the
core's Notifier and Mailer, in the house's brand.

── Where the credential comes from ─────────────────────────────────────────

Nowhere. There is no key in a vault and none in an environment variable: on
Cloud Run the service account IS the credential, and the metadata server hands
out a token for it. That is the same gesture `coreclient/client.py` already
makes to prove this service's identity to the core, and it keeps invariant 2
true — the BFF holds no secret, because there is no secret to hold.

Locally there is no metadata server and no real project. FIREBASE_AUTH_EMULATOR_HOST
switches the whole thing to the emulator, which mints links for nobody and
needs no credential at all.
"""

import time
from urllib.parse import urlsplit, urlunsplit

import httpx

from app.platform.security.firebase import InvalidToken

_METADATA_TOKEN = (
    "http://metadata.google.internal/computeMetadata/v1/"
    "instance/service-accounts/default/token"
)
# Renewed inside this margin rather than at expiry: a token that dies mid-flight
# fails the call, and the caller cannot tell that from a permission problem.
_SKEW = 300


class LinkNotGenerated(Exception):
    """Identity Platform refused to mint the link."""


class FirebaseAdmin:
    def __init__(self, project: str, emulator_host: str = "", timeout: float = 10.0):
        self._project = project
        self._emulator = emulator_host
        self._timeout = timeout
        self._token = ""
        self._expires_at = 0.0

    def _base(self) -> str:
        if self._emulator:
            return f"http://{self._emulator}/identitytoolkit.googleapis.com/v1"
        return "https://identitytoolkit.googleapis.com/v1"

    async def _access_token(self) -> str:
        if self._emulator:
            return ""
        now = time.time()
        if self._token and now < self._expires_at - _SKEW:
            return self._token
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                _METADATA_TOKEN, headers={"Metadata-Flavor": "Google"}
            )
        resp.raise_for_status()
        body = resp.json()
        self._token = body["access_token"]
        self._expires_at = now + float(body.get("expires_in", 3600))
        return self._token

    async def verification_link(self, email: str, continue_url: str = "") -> str:
        """The ready-to-follow link that proves `email`.

        `returnOobLink` is what makes Identity Platform hand the URL back instead
        of mailing it itself. Without it this call would silently do the thing
        D-7 decided against, and the only symptom would be people receiving two
        different-looking messages.
        """
        payload: dict[str, object] = {
            "requestType": "VERIFY_EMAIL",
            "email": email,
            "returnOobLink": True,
        }
        if continue_url:
            payload["continueUrl"] = continue_url

        headers = {"Content-Type": "application/json"}
        token = await self._access_token()
        if token:
            headers["Authorization"] = f"Bearer {token}"

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(
                f"{self._base()}/projects/{self._project}/accounts:sendOobCode",
                json=payload,
                headers=headers,
            )
        if resp.status_code != 200:
            # The body is carried into the message on purpose. Identity Platform
            # answers 400 with EMAIL_NOT_FOUND, INVALID_EMAIL and
            # ADMIN_ONLY_OPERATION alike, and a bare "400" sends whoever reads
            # the log looking at permissions when the address simply does not
            # exist.
            raise LinkNotGenerated(f"HTTP {resp.status_code}: {resp.text[:300]}")
        link = resp.json().get("oobLink", "")
        if not link:
            # A 200 with no link is the failure that would otherwise travel all
            # the way to an e-mail with a dead button in it.
            raise LinkNotGenerated("Identity Platform answered 200 with no oobLink")
        return link


def on_our_domain(link: str, auth_domain: str) -> str:
    """Moves an action link from `<project>.firebaseapp.com` to our own host.

    Identity Platform mints links on its default domain, and the console's
    "customize action URL" — which is the supported way to change that — is
    refused on this project with EMAIL_TEMPLATE_UPDATE_NOT_ALLOWED, in the UI
    and in the API alike. Firebase locks template edits on some projects to
    curb phishing and does not say which.

    It does not matter, because what the link carries is the `oobCode`; the host
    only has to serve the `/__/auth/action` handler, and Firebase Hosting serves
    it on every custom domain of the project. Swapping the host is exactly what
    the console setting does underneath.

    Only the default domain is touched. A link that is already somewhere else —
    the emulator's, a future custom one — comes back untouched, so a
    misconfiguration cannot point real people at a host that serves nothing.
    """
    if not auth_domain:
        return link
    parts = urlsplit(link)
    if not parts.hostname or not parts.hostname.endswith(".firebaseapp.com"):
        return link
    return urlunsplit((parts.scheme, auth_domain, parts.path, parts.query, parts.fragment))


__all__ = ["FirebaseAdmin", "LinkNotGenerated", "InvalidToken", "on_our_domain"]
