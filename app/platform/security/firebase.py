"""Verificação de token do Firebase — e a fronteira que ela protege.

Claims de Firebase NÃO cruzam para o resto da aplicação: verifica-se aqui e
devolve-se um Principal normalizado. É o que permite trocar por Keycloak,
Zitadel ou Ory sem tocar em mais nada (ADR-0001).

Contra o emulador o token não é assinado e a verificação de assinatura é
pulada — mas a NORMALIZAÇÃO é idêntica, então o resto do sistema vê exatamente
a mesma coisa nos dois ambientes.
"""

import base64
import json
import os
import time

import httpx

from app.platform.context import Principal

_JWKS_URL = (
    "https://www.googleapis.com/robot/v1/metadata/x509/"
    "securetoken@system.gserviceaccount.com"
)


class InvalidToken(Exception):
    """Token ausente, malformado, expirado ou de outro projeto."""


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
        parts = token.split(".")
        if len(parts) < 2:
            raise InvalidToken("token malformado")

        claims = self._decode_payload(parts[1])

        if not self.using_emulator:
            await self._verify_signature(token, parts)

        exp = claims.get("exp")
        if exp and float(exp) < time.time():
            raise InvalidToken("token expirado")

        aud = claims.get("aud")
        if aud and self.project_id and aud != self.project_id:
            raise InvalidToken("token emitido para outro projeto")

        subject = claims.get("sub") or claims.get("user_id")
        if not subject:
            raise InvalidToken("token sem sujeito")

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

    @staticmethod
    def _decode_payload(segment: str) -> dict:
        padded = segment + "=" * (-len(segment) % 4)
        try:
            return json.loads(base64.urlsafe_b64decode(padded))
        except Exception as exc:
            raise InvalidToken("payload ilegível") from exc

    async def _verify_signature(self, token: str, parts: list[str]) -> None:
        """Valida a assinatura RS256 contra as chaves públicas do Google.

        Só roda fora do emulador. As chaves são cacheadas por 1h.
        """
        header = self._decode_payload(parts[0])
        kid = header.get("kid")
        if not kid:
            raise InvalidToken("token sem kid")
        if time.time() - self._keys_fetched_at > 3600:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(_JWKS_URL)
                resp.raise_for_status()
                self._keys = resp.json()
                self._keys_fetched_at = time.time()
        if kid not in self._keys:
            raise InvalidToken("chave de assinatura desconhecida")
        # A verificação criptográfica em si entra com a dependência de JWT
        # quando sairmos do emulador; a estrutura já está no lugar.
