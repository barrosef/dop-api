"""The core's gRPC client: deadline, retry and context propagation.

The ADR-0016 rule this module materializes: the BFF has NO database. Everything
that needs state goes through here. If somebody adds a Postgres driver to the
BFF, the boundary dies in three weeks.
"""

import os
import time
import uuid
from collections.abc import Sequence

import grpc
import httpx

from app.coreclient import callauth
from app.platform.context import auth_ctx, current_request_id
from app.settings import settings

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


class _ServiceIdentity(grpc.AuthMetadataPlugin):
    """Attaches this service's own identity to every call to the core.

    Cloud Run checks it before the core's process runs: without the invoker
    permission the request is refused at the platform, and nothing of ours
    executes. It answers "may this caller invoke this service" — a different
    question from the person's token (who is this human) and from the signed
    assertion (which component is asserting). ADR-0029 keeps all three.

    The header is X-Serverless-Authorization, not Authorization, and that is the
    whole reason this class exists rather than gRPC's built-in call credentials.
    `Authorization` already carries the person's token on the way to the core;
    Cloud Run reads this second header precisely so the two do not fight. Sending
    the service token in `Authorization` would overwrite the person's, and the
    symptom — every call arriving unauthenticated — is silent.

    The token is cached: it lives an hour, and minting one per RPC would add a
    metadata-server round trip to every request.
    """

    _SKEW = 300  # refresh five minutes early rather than racing the expiry

    def __init__(self, audience: str):
        self._audience = audience
        self._token = ""
        self._expires_at = 0.0

    # The metadata server, not a Google auth library. It is the documented way to
    # get an identity token on Cloud Run, it needs no credentials of its own, and
    # it costs no dependency: `google.auth`'s transport pulls in `requests`, and
    # this service already speaks httpx.
    _METADATA = (
        "http://metadata.google.internal/computeMetadata/v1/"
        "instance/service-accounts/default/identity"
    )

    def _fresh_token(self) -> str:
        now = time.time()
        if self._token and now < self._expires_at - self._SKEW:
            return self._token
        response = httpx.get(
            self._METADATA,
            params={"audience": self._audience, "format": "full"},
            headers={"Metadata-Flavor": "Google"},
            timeout=5.0,
        )
        response.raise_for_status()
        self._token = response.text.strip()
        # Google issues these for an hour; the skew above is sized against that.
        self._expires_at = now + 3600
        return self._token

    def __call__(self, context, callback):
        try:
            callback((("x-serverless-authorization", f"Bearer {self._fresh_token()}"),), None)
        except Exception as exc:  # noqa: BLE001 — surfaced to the caller as an RPC error
            callback((), exc)


class CoreClient:
    """A single, shared channel — gRPC multiplexes over HTTP/2."""

    def __init__(self, target: str | None = None):
        self.target = target or os.getenv("CORE_GRPC", "dop-core.dop-local.svc:9090")
        # CORE_AUDIENCE is set only where the core is a Cloud Run service. Its
        # presence is what switches this client from the in-cluster shape
        # (plaintext, no service identity) to the managed one (TLS, plus an
        # identity token the platform checks before the core's process runs).
        #
        # It is derived from configuration rather than guessed from the target,
        # because "the hostname ends in run.app" is the kind of inference that is
        # right until somebody puts a custom domain in front of it.
        self.audience = os.getenv("CORE_AUDIENCE", "")
        self._channel: grpc.aio.Channel | None = None

    async def start(self) -> None:
        import json

        options = [
            ("grpc.service_config", json.dumps(_RETRY_POLICY)),
            ("grpc.keepalive_time_ms", 30_000),
        ]
        if self.audience:
            # Cloud Run terminates TLS and speaks HTTP/2; a plaintext channel is
            # refused before anything of ours runs. The service identity rides
            # along as a call credential so every RPC carries it without any
            # caller having to remember.
            credentials = grpc.composite_channel_credentials(
                grpc.ssl_channel_credentials(),
                grpc.metadata_call_credentials(_ServiceIdentity(self.audience)),
            )
            self._channel = grpc.aio.secure_channel(self.target, credentials, options=options)
        else:
            self._channel = grpc.aio.insecure_channel(self.target, options=options)

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
        session_id: str = "",
        raw_token: str = "",
    ) -> Sequence[tuple[str, str]]:
        """Builds the metadata contract WITHOUT depending on auth_ctx.

        It exists because the authorization resolver runs BEFORE auth_ctx is
        filled in — it is precisely the one that discovers the user_id and the
        role. Without this way in, the first call to the core would go out with
        no active account and the core's interceptor would refuse it.
        """
        md = [("x-request-id", current_request_id() or uuid.uuid4().hex)]
        # The PROOF (ADR-0029). It goes on every call, including the ones with
        # no actor yet — the resolver's — because what it proves there is that
        # the caller is the edge, which is the question those calls raise.
        key = settings.call_auth_key
        if key:
            md.append((
                "x-dop-assertion",
                callauth.sign(
                    key,
                    actor_id=user_id,
                    actor_kind=actor_kind if (user_id or account_id) else "system",
                    account_id=account_id,
                    session_id=session_id,
                ),
            ))
        if raw_token:
            # The person's own token, forwarded whole. The core verifies it
            # against the identity provider — a signature neither of us controls.
            md.append(("authorization", f"Bearer {raw_token}"))
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
                # The SESSION, for the second factor's step-up (ADR-0027 §5).
                # It travels like every other field here — with the limit P-18
                # describes, which this feature makes load-bearing.
                ("x-session-id", session_id),
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
            session_id=ctx.principal.session_id,
            raw_token=ctx.raw_token,
        )

    @staticmethod
    def idempotency_key() -> str:
        """The key for a write RPC — repeating must not duplicate the effect."""
        return uuid.uuid4().hex


core = CoreClient()
