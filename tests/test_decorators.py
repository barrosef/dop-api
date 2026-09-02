"""Testes dos transversais — o comportamento que os routers dependem."""

import pytest
from fastapi import HTTPException

from app.platform.context import AuthContext, Principal, auth_ctx
from app.platform.logging.config import AUTO_MASK, REDACTED, _mask_processor
from app.platform.security.decorator import account_scoped, require_grant, require_role


def _ctx(account_id="", role="", grants=None):
    return AuthContext(
        principal=Principal(subject="sub-1", email="dev@dop.local"),
        user_id="u-1",
        account_id=account_id,
        role=role,
        grants=grants or {},
    )


class TestAccountScoped:
    async def test_recusa_sem_conta_ativa(self):
        """Regra do SP-0: requisição sem account ativa é inválida."""
        token = auth_ctx.set(_ctx(account_id=""))

        @account_scoped
        async def handler():
            return "ok"

        with pytest.raises(HTTPException) as exc:
            await handler()
        assert exc.value.status_code == 400
        auth_ctx.reset(token)

    async def test_aceita_com_conta_ativa(self):
        token = auth_ctx.set(_ctx(account_id="acct-1"))

        @account_scoped
        async def handler():
            return "ok"

        assert await handler() == "ok"
        auth_ctx.reset(token)

    async def test_recusa_sem_autenticacao(self):
        token = auth_ctx.set(None)

        @account_scoped
        async def handler():
            return "ok"

        with pytest.raises(HTTPException) as exc:
            await handler()
        assert exc.value.status_code == 401
        auth_ctx.reset(token)


class TestRequireRole:
    async def test_recusa_papel_insuficiente(self):
        token = auth_ctx.set(_ctx(account_id="acct-1", role="developer"))

        @require_role("owner", "admin")
        async def handler():
            return "ok"

        with pytest.raises(HTTPException) as exc:
            await handler()
        assert exc.value.status_code == 403
        auth_ctx.reset(token)

    async def test_aceita_papel_valido(self):
        token = auth_ctx.set(_ctx(account_id="acct-1", role="admin"))

        @require_role("owner", "admin")
        async def handler():
            return "ok"

        assert await handler() == "ok"
        auth_ctx.reset(token)


class TestRequireGrant:
    async def test_owner_tem_manage_implicito(self):
        """Sem isso, ninguém conserta uma integração quebrada."""
        token = auth_ctx.set(_ctx(account_id="acct-1", role="owner"))

        @require_grant("manage")
        async def handler(resource_id: str):
            return "ok"

        assert await handler(resource_id="res-1") == "ok"
        auth_ctx.reset(token)

    async def test_developer_sem_concessao_e_recusado(self):
        token = auth_ctx.set(_ctx(account_id="acct-1", role="developer", grants={}))

        @require_grant("use")
        async def handler(resource_id: str):
            return "ok"

        with pytest.raises(HTTPException) as exc:
            await handler(resource_id="res-1")
        assert exc.value.status_code == 403
        auth_ctx.reset(token)

    async def test_developer_com_concessao_passa(self):
        token = auth_ctx.set(
            _ctx(account_id="acct-1", role="developer", grants={"res-1": "use"})
        )

        @require_grant("use")
        async def handler(resource_id: str):
            return "ok"

        assert await handler(resource_id="res-1") == "ok"
        auth_ctx.reset(token)

    async def test_manage_satisfaz_requisito_de_use(self):
        token = auth_ctx.set(
            _ctx(account_id="acct-1", role="developer", grants={"res-1": "manage"})
        )

        @require_grant("use")
        async def handler(resource_id: str):
            return "ok"

        assert await handler(resource_id="res-1") == "ok"
        auth_ctx.reset(token)

    async def test_use_nao_satisfaz_requisito_de_manage(self):
        token = auth_ctx.set(
            _ctx(account_id="acct-1", role="developer", grants={"res-1": "use"})
        )

        @require_grant("manage")
        async def handler(resource_id: str):
            return "ok"

        with pytest.raises(HTTPException):
            await handler(resource_id="res-1")
        auth_ctx.reset(token)


class TestMascaraDeSegredos:
    """Redação de segredos é requisito (F-10), não conveniência."""

    def test_mascara_chaves_sensiveis(self):
        out = _mask_processor(None, None, {"password": "abc", "user": "ed"})
        assert out["password"] == REDACTED
        assert out["user"] == "ed"

    def test_mascara_em_profundidade(self):
        out = _mask_processor(
            None, None, {"payload": {"nested": {"api_key": "sk-123", "ok": 1}}}
        )
        assert out["payload"]["nested"]["api_key"] == REDACTED
        assert out["payload"]["nested"]["ok"] == 1

    def test_mascara_dentro_de_lista(self):
        out = _mask_processor(None, None, {"items": [{"token": "t1"}, {"token": "t2"}]})
        assert all(i["token"] == REDACTED for i in out["items"])

    def test_cobre_as_chaves_do_dominio(self):
        for k in ("api_key", "private_key", "client_secret", "id_token", "cnpj"):
            assert k in AUTO_MASK, f"{k} deveria estar mascarada"
