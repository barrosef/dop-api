"""The test that guards the authentication boundary.

It exists because this verification WAS once incomplete: it fetched the keys,
checked whether the `kid` existed and returned WITHOUT verifying the signature.
Since `kid` is public, anybody could forge a payload with another user's `sub`
and get in as them — in PRODUCTION. It went unnoticed because the whole of
development runs against the emulator, where that path does not even execute.

That is why these tests exercise the PRODUCTION mode with real keys generated
here, and not the emulator. A test that only exercised the emulator would
declare green exactly the hole that let it through.
"""

import base64
import datetime
import hashlib
import hmac
import json
import time

import jwt
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509 import CertificateBuilder, Name, NameAttribute, random_serial_number
from cryptography.x509.oid import NameOID

from app.platform.security.firebase import FirebaseVerifier, InvalidToken

PROJETO = "dop-test"
KID = "key-de-teste"


def _key_pair():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = Name([NameAttribute(NameOID.COMMON_NAME, "teste")])
    agora = datetime.datetime.now(datetime.UTC)
    cert = (
        CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(random_serial_number())
        .not_valid_before(agora - datetime.timedelta(days=1))
        .not_valid_after(agora + datetime.timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    pem = cert.public_bytes(serialization.Encoding.PEM).decode()
    return key, pem


@pytest.fixture
def verifier():
    """A verifier in PRODUCTION mode, with the certificate already cached.

    `emulator_host=""` is explicit: without it, the environment variable
    exported on the machine of whoever runs the test would turn off the most
    important guarantee with nobody noticing.
    """
    key, pem = _key_pair()
    v = FirebaseVerifier(PROJETO, emulator_host="")
    v._keys = {KID: pem}
    v._keys_fetched_at = time.time()
    return v, key


def _token(key, **override):
    claims = {
        "sub": "usuario-1",
        "aud": PROJETO,
        "iss": f"https://securetoken.google.com/{PROJETO}",
        "iat": int(time.time()),
        "exp": int(time.time()) + 3600,
        "email": "dev@dop.local",
        "firebase": {"sign_in_provider": "password"},
    }
    claims.update(override)
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": KID})


def _forjado(**override):
    """A token with a plausible header and a GARBAGE signature — the real attack."""
    claims = {
        "sub": "vitima",
        "aud": PROJETO,
        "iss": f"https://securetoken.google.com/{PROJETO}",
        "iat": int(time.time()),
        "exp": int(time.time()) + 3600,
    }
    claims.update(override)

    def b64(d):
        return base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")

    h = b64({"alg": "RS256", "kid": KID, "typ": "JWT"})
    p = b64(claims)
    return f"{h}.{p}.{base64.urlsafe_b64encode(b'lixo').decode().rstrip('=')}"


class TestSignature:
    async def test_a_legitimate_token_passes(self, verifier):
        v, key = verifier
        p = await v.verify(_token(key))
        assert p.subject == "usuario-1"
        assert p.providers == ["password"]

    async def test_a_forged_signature_is_refused(self, verifier):
        """The bypass that used to exist. If this test starts failing, it is back."""
        v, _ = verifier
        with pytest.raises(InvalidToken):
            await v.verify(_forjado())

    async def test_a_signature_from_another_key_is_refused(self, verifier):
        v, _ = verifier
        intrusa, _pem = _key_pair()
        with pytest.raises(InvalidToken):
            await v.verify(_token(intrusa))

    async def test_alg_none_is_refused(self, verifier):
        """Algorithm confusion: the token does not choose how it is validated."""
        v, _ = verifier

        def b64(d):
            return base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")

        sem_alg = f"{b64({'alg': 'none', 'kid': KID})}.{b64({'sub': 'x', 'aud': PROJETO})}."
        with pytest.raises(InvalidToken):
            await v.verify(sem_alg)

    async def test_hs256_is_refused(self, verifier):
        """HS256 using the PUBLIC key as the secret — the classic attack.

        The token is built by hand because PyJWT itself refuses to create it: it
        has a guard against signing HS* with material that looks like an
        asymmetric key. Asking it to generate the attack would prove ITS guard,
        not ours.
        """
        v, _ = verifier
        _chave, pem = _key_pair()

        def b64(b: bytes) -> str:
            return base64.urlsafe_b64encode(b).decode().rstrip("=")

        cabecalho = b64(json.dumps({"alg": "HS256", "kid": KID}).encode())
        body = b64(json.dumps({"sub": "x", "aud": PROJETO}).encode())
        assinatura = b64(
            hmac.new(pem.encode(), f"{cabecalho}.{body}".encode(), hashlib.sha256).digest()
        )
        with pytest.raises(InvalidToken):
            await v.verify(f"{cabecalho}.{body}.{assinatura}")


class TestClaims:
    async def test_an_expired_token_is_refused(self, verifier):
        v, key = verifier
        antigo = int(time.time()) - 7200
        with pytest.raises(InvalidToken):
            await v.verify(_token(key, exp=antigo, iat=antigo - 60))

    async def test_another_project_is_refused(self, verifier):
        v, key = verifier
        with pytest.raises(InvalidToken):
            await v.verify(_token(key, aud="outro-project"))

    async def test_another_issuer_is_refused(self, verifier):
        v, key = verifier
        with pytest.raises(InvalidToken):
            await v.verify(_token(key, iss="https://evil.example.com"))

    async def test_with_no_project_it_fails_closed(self):
        """With no project there is no audience to check; accepting would be not checking."""
        key, pem = _key_pair()
        v = FirebaseVerifier("", emulator_host="")
        v._keys, v._keys_fetched_at = {KID: pem}, time.time()
        with pytest.raises(InvalidToken):
            await v.verify(_token(key))


class TestLeaking:
    async def test_the_error_message_does_not_carry_the_token(self, verifier):
        """A token in an error message is a credential at rest, on its way to the log."""
        v, _ = verifier
        forjado = _forjado()
        with pytest.raises(InvalidToken) as e:
            await v.verify(forjado)
        text = str(e.value)
        assert forjado not in text
        for parte in forjado.split("."):
            assert parte not in text
        assert "vitima" not in text


class TestEmulator:
    """The emulator issues `alg: none`; the signature is skipped — but only it."""

    async def test_the_emulators_token_passes(self):
        v = FirebaseVerifier(PROJETO, emulator_host="localhost:9099")

        def b64(d):
            return base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")

        t = (
            f"{b64({'alg': 'none', 'typ': 'JWT'})}."
            f"{b64({'sub': 'emu-1', 'aud': PROJETO, 'exp': int(time.time()) + 3600})}."
        )
        p = await v.verify(t)
        assert p.subject == "emu-1"

    async def test_the_emulator_still_refuses_another_project(self):
        """Skipping the signature is no excuse for accepting anything."""
        v = FirebaseVerifier(PROJETO, emulator_host="localhost:9099")

        def b64(d):
            return base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")

        t = f"{b64({'alg': 'none'})}.{b64({'sub': 'x', 'aud': 'outro'})}."
        with pytest.raises(InvalidToken):
            await v.verify(t)
