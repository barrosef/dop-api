"""Verifying a Firebase token — and the boundary that verification protects.

Firebase claims do NOT cross into the rest of the application: they are verified
here and a normalized Principal is returned. It is what allows swapping in
Keycloak, Zitadel or Ory without touching anything else (ADR-0001).

Against the emulator the token is issued with `alg: none` and the SIGNATURE
check is skipped — but only that one, and only when
`FIREBASE_AUTH_EMULATOR_HOST` is set. The decision comes from the ENVIRONMENT,
never from the token's content: a token that declares itself unsigned must not
choose its own validation path. The normalization is identical in both modes, so
the rest of the system sees the same thing in any environment.

A piece of history worth recording: this verification was once incomplete — it
fetched the keys, checked whether the `kid` existed and returned WITHOUT
verifying the signature. Since `kid` is public, anybody could forge a payload
with another user's `sub` and get in as them. It went unnoticed because in
development everything runs against the emulator, where that path does not even
execute. If you are going to touch this, the test that matters is
`tests/test_firebase_verifier.py`.
"""

import base64
import json
import os
import time

import httpx
import jwt
from cryptography.x509 import load_pem_x509_certificate

from app.platform.context import Principal

# Firebase publishes X.509 CERTIFICATES, not a JWKS: the public key comes out of
# the certificate, not out of an (n, e) pair.
_CERTS_URL = (
    "https://www.googleapis.com/robot/v1/metadata/x509/"
    "securetoken@system.gserviceaccount.com"
)

# Clock tolerance, in both directions. Without it, NTP drift becomes an
# intermittent "expired token"; too large, and a stolen token outlives its
# expiry.
_CLOCK_SKEW_S = 60

_ALGORITHMS = ["RS256"]


class InvalidToken(Exception):
    """A token that is missing, malformed, expired or from another project."""


class FirebaseVerifier:
    def __init__(self, project_id: str, emulator_host: str | None = None):
        self.project_id = project_id
        self.emulator_host = emulator_host or os.getenv("FIREBASE_AUTH_EMULATOR_HOST", "")
        self._keys: dict[str, str] = {}
        self._keys_fetched_at = 0.0

    @property
    def using_emulator(self) -> bool:
        return bool(self.emulator_host)

    async def verify(self, raw: str) -> Principal:
        token = raw.removeprefix("Bearer ").strip()
        if not token:
            raise InvalidToken("missing token")

        if self.using_emulator:
            # Only the emulator gets here, and only it issues `alg: none`.
            claims = self._emulator_claims(token)
        else:
            claims = await self._verified_claims(token)

        subject = claims.get("sub") or claims.get("user_id")
        if not subject:
            raise InvalidToken("token with no subject")

        fb = claims.get("firebase") or {}
        providers = list((fb.get("identities") or {}).keys())
        if sip := fb.get("sign_in_provider"):
            providers.append(sip)

        return Principal(
            subject=subject,
            email=claims.get("email", ""),
            email_verified=bool(claims.get("email_verified")),
            name=claims.get("name", ""),
            avatar_url=claims.get("picture", ""),
            providers=providers,
        )

    async def _verified_claims(self, token: str) -> dict:
        """The PRODUCTION path: signature, issuer, audience and validity.

        It fails CLOSED with no `project_id`: without it there is no audience to
        check, and accepting any project is the same as checking nothing.
        """
        if not self.project_id:
            raise InvalidToken("the verifier has no project configured")

        key = await self._public_key(token)
        try:
            return jwt.decode(
                token,
                key=key,
                algorithms=_ALGORITHMS,
                audience=self.project_id,
                issuer=f"https://securetoken.google.com/{self.project_id}",
                leeway=_CLOCK_SKEW_S,
                options={"require": ["exp", "iat", "aud", "iss"]},
            )
        except jwt.InvalidTokenError as exc:
            # The message carries NEITHER the token nor a piece of it: a token
            # in a log is a credential at rest.
            raise InvalidToken("invalid token") from exc

    async def _public_key(self, token: str):
        """Resolves the key by its `kid`, with a cache and a refresh on rotation."""
        try:
            header = jwt.get_unverified_header(token)
        except jwt.InvalidTokenError as exc:
            raise InvalidToken("malformed token") from exc

        alg = header.get("alg")
        if alg not in _ALGORITHMS:
            # An explicit refusal of `none` and of the HS* family: it is the
            # algorithm confusion, and it has to die before anything else.
            raise InvalidToken("signature algorithm not accepted")
        kid = header.get("kid")
        if not kid:
            raise InvalidToken("token with no kid")

        if kid not in self._keys:
            await self._fetch_certificates()
        cert_pem = self._keys.get(kid)
        if not cert_pem:
            raise InvalidToken("unknown signing key")
        return load_pem_x509_certificate(cert_pem.encode()).public_key()

    async def _fetch_certificates(self) -> None:
        """Fetches the certificates, with a brake.

        The brake exists because an unknown `kid` is what triggers the fetch:
        without it, a flood of tokens with invented `kid`s becomes a
        denial-of-service attack against Google using our IP.
        """
        if time.time() - self._keys_fetched_at < 30:
            return
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(_CERTS_URL)
            resp.raise_for_status()
            self._keys = resp.json()
            self._keys_fetched_at = time.time()

    def _emulator_claims(self, token: str) -> dict:
        """The emulator: no signature, but the REST still holds.

        The audience and the expiry are still checked — skipping the signature is
        no excuse for accepting a token from another project or an expired one,
        and it is what keeps the local behaviour close to production's.
        """
        parts = token.split(".")
        if len(parts) < 2:
            raise InvalidToken("malformed token")
        claims = self._decode_payload(parts[1])

        exp = claims.get("exp")
        if exp and float(exp) + _CLOCK_SKEW_S < time.time():
            raise InvalidToken("expired token")
        aud = claims.get("aud")
        if aud and self.project_id and aud != self.project_id:
            raise InvalidToken("token issued for another project")
        return claims

    @staticmethod
    def _decode_payload(segment: str) -> dict:
        padded = segment + "=" * (-len(segment) % 4)
        try:
            return json.loads(base64.urlsafe_b64decode(padded))
        except Exception as exc:
            raise InvalidToken("unreadable payload") from exc
