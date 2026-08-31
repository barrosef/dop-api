"""Verificação de token do Firebase — e a fronteira que ela protege.

Claims de Firebase NÃO cruzam para o resto da aplicação: verifica-se aqui e
devolve-se um Principal normalizado. É o que permite trocar por Keycloak,
Zitadel ou Ory sem tocar em mais nada (ADR-0001).

Contra o emulador o token é emitido com `alg: none` e a verificação de
ASSINATURA é pulada — mas só ela, e só quando `FIREBASE_AUTH_EMULATOR_HOST`
está definido. A decisão vem do AMBIENTE, nunca do conteúdo do token: um token
que se declara não assinado não pode escolher o próprio caminho de validação.
A normalização é idêntica nos dois modos, então o resto do sistema vê a mesma
coisa em qualquer ambiente.

Histórico que vale ficar registrado: esta verificação já esteve incompleta —
buscava as chaves, conferia se o `kid` existia e devolvia SEM verificar a
assinatura. Como `kid` é público, qualquer pessoa forjava um payload com o
`sub` de outro usuário e entrava como ele. Passou despercebido porque no
desenvolvimento tudo roda contra o emulador, onde esse caminho nem executa.
Se for mexer aqui, o teste que importa é `tests/test_firebase_verifier.py`.
"""

import base64
import json
import os
import time

import httpx
import jwt
from cryptography.x509 import load_pem_x509_certificate

from app.platform.context import Principal

# O Firebase publica CERTIFICADOS X.509, não um JWKS: a chave pública sai do
# certificado, não de um par (n, e).
_CERTS_URL = (
    "https://www.googleapis.com/robot/v1/metadata/x509/"
    "securetoken@system.gserviceaccount.com"
)

# Tolerância de relógio, nos dois sentidos. Sem ela, deriva de NTP vira "token
# expirado" intermitente; grande demais, token roubado sobrevive à expiração.
_CLOCK_SKEW_S = 60

_ALGORITMOS = ["RS256"]


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
        if not token:
            raise InvalidToken("token ausente")

        if self.using_emulator:
            # Só o emulador chega aqui, e só ele emite `alg: none`.
            claims = self._claims_do_emulador(token)
        else:
            claims = await self._claims_verificadas(token)

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

    async def _claims_verificadas(self, token: str) -> dict:
        """O caminho de PRODUÇÃO: assinatura, emissor, audiência e validade.

        Falha FECHADA sem `project_id`: sem ele não há audiência para conferir,
        e aceitar qualquer projeto é o mesmo que não conferir nada.
        """
        if not self.project_id:
            raise InvalidToken("verificador sem projeto configurado")

        chave = await self._chave_publica(token)
        try:
            return jwt.decode(
                token,
                key=chave,
                algorithms=_ALGORITMOS,
                audience=self.project_id,
                issuer=f"https://securetoken.google.com/{self.project_id}",
                leeway=_CLOCK_SKEW_S,
                options={"require": ["exp", "iat", "aud", "iss"]},
            )
        except jwt.InvalidTokenError as exc:
            # A mensagem NÃO carrega o token nem pedaço dele: token em log é
            # credencial em repouso.
            raise InvalidToken("token inválido") from exc

    async def _chave_publica(self, token: str):
        """Resolve a chave pelo `kid`, com cache e renovação na rotação."""
        try:
            cabecalho = jwt.get_unverified_header(token)
        except jwt.InvalidTokenError as exc:
            raise InvalidToken("token malformado") from exc

        alg = cabecalho.get("alg")
        if alg not in _ALGORITMOS:
            # Recusa explícita de `none` e da família HS*: é a confusão de
            # algoritmo, e ela precisa morrer antes de qualquer outra coisa.
            raise InvalidToken("algoritmo de assinatura não aceito")
        kid = cabecalho.get("kid")
        if not kid:
            raise InvalidToken("token sem kid")

        if kid not in self._keys:
            await self._buscar_certificados()
        cert_pem = self._keys.get(kid)
        if not cert_pem:
            raise InvalidToken("chave de assinatura desconhecida")
        return load_pem_x509_certificate(cert_pem.encode()).public_key()

    async def _buscar_certificados(self) -> None:
        """Busca os certificados, com freio.

        O freio existe porque `kid` desconhecido é o gatilho da busca: sem ele,
        uma enxurrada de tokens com `kid` inventado vira ataque de negação de
        serviço contra o Google usando o nosso IP.
        """
        if time.time() - self._keys_fetched_at < 30:
            return
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(_CERTS_URL)
            resp.raise_for_status()
            self._keys = resp.json()
            self._keys_fetched_at = time.time()

    def _claims_do_emulador(self, token: str) -> dict:
        """Emulador: sem assinatura, mas o RESTO continua valendo.

        Audiência e expiração seguem conferidas — pular a assinatura não é
        desculpa para aceitar token de outro projeto ou vencido, e é isso que
        mantém o comportamento local parecido com o de produção.
        """
        parts = token.split(".")
        if len(parts) < 2:
            raise InvalidToken("token malformado")
        claims = self._decode_payload(parts[1])

        exp = claims.get("exp")
        if exp and float(exp) + _CLOCK_SKEW_S < time.time():
            raise InvalidToken("token expirado")
        aud = claims.get("aud")
        if aud and self.project_id and aud != self.project_id:
            raise InvalidToken("token emitido para outro projeto")
        return claims

    @staticmethod
    def _decode_payload(segment: str) -> dict:
        padded = segment + "=" * (-len(segment) % 4)
        try:
            return json.loads(base64.urlsafe_b64decode(padded))
        except Exception as exc:
            raise InvalidToken("payload ilegível") from exc
