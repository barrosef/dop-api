"""O teste que guarda a fronteira de autenticação.

Existe porque esta verificação JÁ esteve incompleta: ela buscava as keys,
conferia se o `kid` existia e devolvia SEM verificar a assinatura. Como `kid` é
público, qualquer pessoa forjava um payload com o `sub` de outro usuário e
entrava como ele — em PRODUÇÃO. Passou despercebido porque o desenvolvimento
inteiro roda contra o emulador, onde esse caminho nem executa.

Por isso estes testes exercitam o modo de PRODUÇÃO com keys de verdade
geradas aqui, e não o emulador. Um teste que só exercitasse o emulador
declararia verde exatamente o buraco que deixou passar.
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
def verificador():
    """Verificador em modo PRODUÇÃO, com o certificado já em cache.

    `emulator_host=""` é explícito: sem isso, a variável de ambiente exportada
    na máquina de quem roda o teste desligaria a garantia mais importante sem
    ninguém perceber.
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
    """Token com cabeçalho plausível e assinatura LIXO — o ataque real."""
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


class TestAssinatura:
    async def test_token_legitimo_passa(self, verificador):
        v, key = verificador
        p = await v.verify(_token(key))
        assert p.subject == "usuario-1"
        assert p.providers == ["password"]

    async def test_assinatura_forjada_e_recusada(self, verificador):
        """O bypass que existia. Se este teste passar a falhar, ele voltou."""
        v, _ = verificador
        with pytest.raises(InvalidToken):
            await v.verify(_forjado())

    async def test_assinatura_de_outra_chave_e_recusada(self, verificador):
        v, _ = verificador
        intrusa, _pem = _key_pair()
        with pytest.raises(InvalidToken):
            await v.verify(_token(intrusa))

    async def test_alg_none_e_recusado(self, verificador):
        """Confusão de algoritmo: o token não escolhe como é validado."""
        v, _ = verificador

        def b64(d):
            return base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")

        sem_alg = f"{b64({'alg': 'none', 'kid': KID})}.{b64({'sub': 'x', 'aud': PROJETO})}."
        with pytest.raises(InvalidToken):
            await v.verify(sem_alg)

    async def test_hs256_e_recusado(self, verificador):
        """HS256 usando a key PÚBLICA como segredo — o ataque clássico.

        O token é montado à mão porque o próprio PyJWT se recusa a criá-lo: ele
        tem guarda contra assinar HS* com material que parece key assimétrica.
        Pedir a ele que gerasse o ataque provaria a guarda DELE, não a nossa.
        """
        v, _ = verificador
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
    async def test_expirado_e_recusado(self, verificador):
        v, key = verificador
        antigo = int(time.time()) - 7200
        with pytest.raises(InvalidToken):
            await v.verify(_token(key, exp=antigo, iat=antigo - 60))

    async def test_outro_projeto_e_recusado(self, verificador):
        v, key = verificador
        with pytest.raises(InvalidToken):
            await v.verify(_token(key, aud="outro-project"))

    async def test_outro_emissor_e_recusado(self, verificador):
        v, key = verificador
        with pytest.raises(InvalidToken):
            await v.verify(_token(key, iss="https://evil.example.com"))

    async def test_sem_projeto_falha_fechado(self):
        """Sem project não há audiência para conferir; aceitar seria não conferir."""
        key, pem = _key_pair()
        v = FirebaseVerifier("", emulator_host="")
        v._keys, v._keys_fetched_at = {KID: pem}, time.time()
        with pytest.raises(InvalidToken):
            await v.verify(_token(key))


class TestVazamento:
    async def test_mensagem_de_erro_nao_carrega_o_token(self, verificador):
        """Token em mensagem de err é credencial em repouso, indo para o log."""
        v, _ = verificador
        forjado = _forjado()
        with pytest.raises(InvalidToken) as e:
            await v.verify(forjado)
        text = str(e.value)
        assert forjado not in text
        for parte in forjado.split("."):
            assert parte not in text
        assert "vitima" not in text


class TestEmulador:
    """O emulador emite `alg: none`; a assinatura é pulada — mas só ela."""

    async def test_token_do_emulador_passa(self):
        v = FirebaseVerifier(PROJETO, emulator_host="localhost:9099")

        def b64(d):
            return base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")

        t = (
            f"{b64({'alg': 'none', 'typ': 'JWT'})}."
            f"{b64({'sub': 'emu-1', 'aud': PROJETO, 'exp': int(time.time()) + 3600})}."
        )
        p = await v.verify(t)
        assert p.subject == "emu-1"

    async def test_emulador_ainda_recusa_outro_projeto(self):
        """Pular assinatura não é desculpa para aceitar qualquer coisa."""
        v = FirebaseVerifier(PROJETO, emulator_host="localhost:9099")

        def b64(d):
            return base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")

        t = f"{b64({'alg': 'none'})}.{b64({'sub': 'x', 'aud': 'outro'})}."
        with pytest.raises(InvalidToken):
            await v.verify(t)
