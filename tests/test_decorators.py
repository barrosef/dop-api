"""Tests of the cross-cutting concerns — the behaviour the routers depend on."""

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
    async def test_it_refuses_with_no_active_account(self):
        """SP-0's rule: a request with no active account is invalid."""
        token = auth_ctx.set(_ctx(account_id=""))

        @account_scoped
        async def handler():
            return "ok"

        with pytest.raises(HTTPException) as exc:
            await handler()
        assert exc.value.status_code == 400
        auth_ctx.reset(token)

    async def test_it_accepts_with_an_active_account(self):
        token = auth_ctx.set(_ctx(account_id="acct-1"))

        @account_scoped
        async def handler():
            return "ok"

        assert await handler() == "ok"
        auth_ctx.reset(token)

    async def test_it_refuses_with_no_authentication(self):
        token = auth_ctx.set(None)

        @account_scoped
        async def handler():
            return "ok"

        with pytest.raises(HTTPException) as exc:
            await handler()
        assert exc.value.status_code == 401
        auth_ctx.reset(token)


class TestRequireRole:
    async def test_it_refuses_an_insufficient_role(self):
        token = auth_ctx.set(_ctx(account_id="acct-1", role="developer"))

        @require_role("owner", "admin")
        async def handler():
            return "ok"

        with pytest.raises(HTTPException) as exc:
            await handler()
        assert exc.value.status_code == 403
        auth_ctx.reset(token)

    async def test_it_accepts_a_valid_role(self):
        token = auth_ctx.set(_ctx(account_id="acct-1", role="admin"))

        @require_role("owner", "admin")
        async def handler():
            return "ok"

        assert await handler() == "ok"
        auth_ctx.reset(token)


class TestRequireGrant:
    async def test_an_owner_has_implicit_manage(self):
        """Without this, nobody fixes a broken integration."""
        token = auth_ctx.set(_ctx(account_id="acct-1", role="owner"))

        @require_grant("manage")
        async def handler(resource_id: str):
            return "ok"

        assert await handler(resource_id="res-1") == "ok"
        auth_ctx.reset(token)

    async def test_a_developer_with_no_grant_is_refused(self):
        token = auth_ctx.set(_ctx(account_id="acct-1", role="developer", grants={}))

        @require_grant("use")
        async def handler(resource_id: str):
            return "ok"

        with pytest.raises(HTTPException) as exc:
            await handler(resource_id="res-1")
        assert exc.value.status_code == 403
        auth_ctx.reset(token)

    async def test_a_developer_with_a_grant_passes(self):
        token = auth_ctx.set(
            _ctx(account_id="acct-1", role="developer", grants={"res-1": "use"})
        )

        @require_grant("use")
        async def handler(resource_id: str):
            return "ok"

        assert await handler(resource_id="res-1") == "ok"
        auth_ctx.reset(token)

    async def test_manage_satisfies_a_use_requirement(self):
        token = auth_ctx.set(
            _ctx(account_id="acct-1", role="developer", grants={"res-1": "manage"})
        )

        @require_grant("use")
        async def handler(resource_id: str):
            return "ok"

        assert await handler(resource_id="res-1") == "ok"
        auth_ctx.reset(token)

    async def test_use_does_not_satisfy_a_manage_requirement(self):
        token = auth_ctx.set(
            _ctx(account_id="acct-1", role="developer", grants={"res-1": "use"})
        )

        @require_grant("manage")
        async def handler(resource_id: str):
            return "ok"

        with pytest.raises(HTTPException):
            await handler(resource_id="res-1")
        auth_ctx.reset(token)


class TestSecretMasking:
    """Redacting secrets is a requirement (F-10), not a convenience."""

    def test_it_masks_sensitive_keys(self):
        out = _mask_processor(None, None, {"password": "abc", "user": "ed"})
        assert out["password"] == REDACTED
        assert out["user"] == "ed"

    def test_it_masks_at_depth(self):
        out = _mask_processor(
            None, None, {"payload": {"nested": {"api_key": "sk-123", "ok": 1}}}
        )
        assert out["payload"]["nested"]["api_key"] == REDACTED
        assert out["payload"]["nested"]["ok"] == 1

    def test_it_masks_inside_a_list(self):
        out = _mask_processor(None, None, {"items": [{"token": "t1"}, {"token": "t2"}]})
        assert all(i["token"] == REDACTED for i in out["items"])

    def test_it_covers_the_domains_keys(self):
        for k in ("api_key", "private_key", "client_secret", "id_token", "cnpj"):
            assert k in AUTO_MASK, f"{k} deveria estar mascarada"
