"""The core's gRPC client: deadline, retry and context propagation.

The ADR-0016 rule this module materializes: the BFF has NO database. Everything
that needs state goes through here. If somebody adds a Postgres driver to the
BFF, the boundary dies in three weeks.
"""

import os
import uuid
from collections.abc import Sequence

import grpc

from app.platform.context import auth_ctx, current_request_id

DEFAULT_DEADLINE_S = float(os.getenv("CORE_DEADLINE_S", "10"))

# Retry on the channel: only for REALLY retryable errors. Writes carry an
# idempotency_key (ADR-0017), so repeating is safe.
_RETRY_POLICY = {
    "methodConfig": [
        {
            "name": [{"service": ""}],
            "retryPolicy": {
                "maxAttempts": 4,
                "initialBackoff": "0.1s",
                "maxBackoff": "2s",
                "backoffMultiplier": 2,
                "retryableStatusCodes": ["UNAVAILABLE", "DEADLINE_EXCEEDED"],
            },
        }
    ]
}


class CoreClient:
    """A single, shared channel — gRPC multiplexes over HTTP/2."""

    def __init__(self, target: str | None = None):
        self.target = target or os.getenv("CORE_GRPC", "dop-core.dop-local.svc:9090")
        self._channel: grpc.aio.Channel | None = None

    async def start(self) -> None:
        import json

        self._channel = grpc.aio.insecure_channel(
            self.target,
            options=[
                ("grpc.service_config", json.dumps(_RETRY_POLICY)),
                ("grpc.keepalive_time_ms", 30_000),
            ],
        )

    async def stop(self) -> None:
        if self._channel is not None:
            await self._channel.close()

    @property
    def channel(self) -> grpc.aio.Channel:
        if self._channel is None:
            raise RuntimeError("CoreClient not started")
        return self._channel

    @staticmethod
    def metadata_for(
        *,
        user_id: str = "",
        account_id: str = "",
        actor_name: str = "",
        actor_kind: str = "user",
    ) -> Sequence[tuple[str, str]]:
        """Builds the metadata contract WITHOUT depending on auth_ctx.

        It exists because the authorization resolver runs BEFORE auth_ctx is
        filled in — it is precisely the one that discovers the user_id and the
        role. Without this way in, the first call to the core would go out with
        no active account and the core's interceptor would refuse it.
        """
        md = [("x-request-id", current_request_id() or uuid.uuid4().hex)]
        if user_id or account_id:
            md += [
                ("x-account-id", account_id),
                ("x-actor-id", user_id),
                # The actor's KIND, not only the id. The core derives
                # authorship from here, and the whole platform starts from "the
                # dev is a manager of agents": recording the agent's answer as
                # the human's speech corrupts the event log, which is the truth
                # (ADR-0006).
                ("x-actor-kind", actor_kind),
                ("x-actor-name", actor_name),
            ]
        return md

    @classmethod
    def metadata(cls) -> Sequence[tuple[str, str]]:
        """Propagates the call's context — who, in which account.

        The EDGE fills it in; the domain trusts it. It is the same contract the
        core's UnaryCallContext interceptor reads.
        """
        ctx = auth_ctx.get()
        if ctx is None:
            return cls.metadata_for()
        return cls.metadata_for(
            user_id=ctx.user_id,
            account_id=ctx.account_id,
            actor_name=ctx.principal.name or ctx.principal.email,
        )

    @staticmethod
    def idempotency_key() -> str:
        """The key for a write RPC — repeating must not duplicate the effect."""
        return uuid.uuid4().hex


core = CoreClient()
