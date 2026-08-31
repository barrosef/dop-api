"""Cliente gRPC do core: deadline, retry e propagação de contexto.

Regra da ADR-0016 que este módulo materializa: o BFF NÃO tem banco. Tudo que
precisa de estado passa por aqui. Se alguém adicionar um driver de Postgres ao
BFF, a fronteira morre em três semanas.
"""

import os
import uuid
from collections.abc import Sequence

import grpc

from app.platform.context import auth_ctx, current_request_id

DEFAULT_DEADLINE_S = float(os.getenv("CORE_DEADLINE_S", "10"))

# Retry no canal: só para erros REALMENTE retentáveis. Escritas carregam
# idempotency_key (ADR-0017), então repetir é seguro.
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
    """Canal único e compartilhado — gRPC multiplexa em HTTP/2."""

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
            raise RuntimeError("CoreClient não iniciado")
        return self._channel

    @staticmethod
    def metadata_for(
        *,
        user_id: str = "",
        account_id: str = "",
        actor_name: str = "",
        actor_kind: str = "user",
    ) -> Sequence[tuple[str, str]]:
        """Monta o contrato de metadados SEM depender do auth_ctx.

        Existe porque o resolver de autorização roda ANTES do auth_ctx ser
        preenchido — é justamente ele quem descobre user_id e papel. Sem esta
        porta de entrada, a primeira chamada ao core sairia sem conta ativa e o
        interceptor do core a recusaria.
        """
        md = [("x-request-id", current_request_id() or uuid.uuid4().hex)]
        if user_id or account_id:
            md += [
                ("x-account-id", account_id),
                ("x-actor-id", user_id),
                # A ESPÉCIE do ator, não só o id. O núcleo deriva a autoria
                # daqui, e a plataforma inteira parte de "o dev é gerente de
                # agentes": gravar a resposta do agente como fala do humano
                # corrompe o log de eventos, que é a verdade (ADR-0006).
                ("x-actor-kind", actor_kind),
                ("x-actor-name", actor_name),
            ]
        return md

    @classmethod
    def metadata(cls) -> Sequence[tuple[str, str]]:
        """Propaga o contexto de chamada — quem, em qual conta.

        A BORDA preenche; o domínio confia. É o mesmo contrato lido pelo
        interceptor UnaryCallContext do core.
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
        """Chave para RPC de escrita — repetir não pode duplicar efeito."""
        return uuid.uuid4().hex


core = CoreClient()
