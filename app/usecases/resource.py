"""Casos de uso de recursos — integrações, skills, fluxos.

Mesma disciplina de `identity` e `hierarchy`: a regra vive aqui, router e
servicer traduzem.

**A regra dura deste módulo: o segredo nunca volta.** `SetCredential` grava no
cofre e devolve apenas uma referência opaca; não existe caminho de leitura do
valor pela borda, e não deve passar a existir. Quem precisa do segredo é o
executor, direto do cofre (ADR-0001). Por isso o valor também não entra em log
nem em mensagem de erro — o mascaramento automático do logger cobre o campo,
mas a primeira linha de defesa é não passar o valor adiante.
"""

import base64

from google.protobuf import struct_pb2
from pydantic import BaseModel, Field

from app.coreclient import stubs
from app.coreclient.client import core
from app.coreclient.convert import call_context_from
from app.coreclient.gen.dop.v1 import resource_pb2
from app.platform.context import auth_ctx
from app.platform.logging.decorator import log
from app.platform.security.decorator import account_scoped, require_role
from app.settings import settings

# Nome ↔ enum num lugar só. Espalhar essa tabela é como as duas pontas passam
# a discordar sobre o que é um "git_flow".
_KIND_POR_NOME = {
    "integration": resource_pb2.Resource.KIND_INTEGRATION,
    "skill": resource_pb2.Resource.KIND_SKILL,
    "workflow": resource_pb2.Resource.KIND_WORKFLOW,
    "git_flow": resource_pb2.Resource.KIND_GIT_FLOW,
}
_NOME_POR_KIND = {v: k for k, v in _KIND_POR_NOME.items()}


def _deadline() -> float:
    return settings.core_deadline_s


def _idempotency(chave: str = "") -> str:
    return chave or core.idempotency_key()


class ResourceSummary(BaseModel):
    id: str
    kind: str
    name: str
    version: int = 1
    config: dict = Field(default_factory=dict)
    # Rótulo opaco derivável da linha — a presença dele diz que HÁ credencial,
    # nunca qual é. Vazio significa integração ainda sem segredo.
    credential_ref: str = ""


class NewResource(BaseModel):
    kind: str = Field(min_length=1)
    name: str = Field(min_length=1)
    # Categoria e provedor vão aqui: a categoria é string minúscula
    # (git | task_manager | agent) porque quem manda no vocabulário é o
    # provedor, não a plataforma (ADR-0013).
    config: dict = Field(default_factory=dict)


class NewCredential(BaseModel):
    """O segredo chega em base64 — bytes no contrato do núcleo.

    Modelo separado de propósito: assim o valor nunca encosta no modelo que a
    borda devolve, e não há como alguém acidentalmente serializá-lo de volta.
    """

    secret_base64: str = Field(min_length=1)


class GrantSummary(BaseModel):
    id: str
    resource_id: str
    user_id: str
    level: str


class NewGrant(BaseModel):
    resource_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    level: str = Field(pattern="^(use|manage)$")


def _resource(r: resource_pb2.Resource) -> ResourceSummary:
    config = {}
    if r.HasField("config"):
        config = {k: _valor(v) for k, v in r.config.fields.items()}
    return ResourceSummary(
        id=r.id,
        kind=_NOME_POR_KIND.get(r.kind, ""),
        name=r.name,
        version=r.version,
        config=config,
        credential_ref=r.credential_ref,
    )


def _valor(v: struct_pb2.Value):
    """Struct do protobuf para Python, só o que a config usa de fato."""
    campo = v.WhichOneof("kind")
    if campo == "string_value":
        return v.string_value
    if campo == "number_value":
        return v.number_value
    if campo == "bool_value":
        return v.bool_value
    if campo == "list_value":
        return [_valor(i) for i in v.list_value.values]
    if campo == "struct_value":
        return {k: _valor(i) for k, i in v.struct_value.fields.items()}
    return None


@log
@account_scoped
async def list_resources(kind: str = "") -> list[ResourceSummary]:
    """Recursos visíveis para o ator.

    O núcleo já FILTRA pelo que o ator pode usar — integração de outra pessoa
    não aparece. A borda não refiltra: duplicar a regra de visibilidade aqui
    seria criar uma segunda verdade que uma hora discorda da primeira.
    """
    ctx = auth_ctx.get()
    pedido = resource_pb2.ListResourcesRequest(ctx=call_context_from(ctx))
    if kind:
        pedido.kind = _KIND_POR_NOME.get(kind, resource_pb2.Resource.KIND_UNSPECIFIED)
    resp = await stubs.resource_stub().ListResources(
        pedido, metadata=core.metadata(), timeout=_deadline()
    )
    return [_resource(r) for r in resp.resources]


@log
@account_scoped
async def get_resource(resource_id: str) -> ResourceSummary:
    ctx = auth_ctx.get()
    r = await stubs.resource_stub().GetResource(
        resource_pb2.GetResourceRequest(ctx=call_context_from(ctx), id=resource_id),
        metadata=core.metadata(),
        timeout=_deadline(),
    )
    return _resource(r)


@log
@account_scoped
async def create_resource(body: NewResource, idempotency_key: str = "") -> ResourceSummary:
    """Cria o recurso SEM credencial — o segredo entra por `set_credential`.

    Separar os dois passos não é cerimônia: mantém o valor fora da requisição
    que cria, que é a mais logada e a mais retentada de todas.
    """
    ctx = auth_ctx.get()
    config = struct_pb2.Struct()
    config.update(body.config)
    r = await stubs.resource_stub().CreateResource(
        resource_pb2.CreateResourceRequest(
            ctx=call_context_from(ctx),
            kind=_KIND_POR_NOME.get(body.kind, resource_pb2.Resource.KIND_UNSPECIFIED),
            name=body.name,
            config=config,
            idempotency_key=_idempotency(idempotency_key),
        ),
        metadata=core.metadata(),
        timeout=_deadline(),
    )
    return _resource(r)


@log
@account_scoped
async def set_credential(resource_id: str, body: NewCredential) -> ResourceSummary:
    """Grava o segredo no cofre e devolve o RECURSO, sem o valor.

    Devolver o recurso (e não só a referência) poupa uma ida de volta ao
    cockpit, que precisa mostrar "credencial configurada". O valor não vem
    junto porque não existe caminho de leitura dele.
    """
    ctx = auth_ctx.get()
    stub = stubs.resource_stub()
    await stub.SetCredential(
        resource_pb2.SetCredentialRequest(
            ctx=call_context_from(ctx),
            resource_id=resource_id,
            secret=base64.b64decode(body.secret_base64),
        ),
        metadata=core.metadata(),
        timeout=_deadline(),
    )
    r = await stub.GetResource(
        resource_pb2.GetResourceRequest(ctx=call_context_from(ctx), id=resource_id),
        metadata=core.metadata(),
        timeout=_deadline(),
    )
    return _resource(r)


@log
@account_scoped
@require_role("owner", "admin")
async def grant_resource(body: NewGrant) -> GrantSummary:
    """Concessão é sempre explícita — recurso credenciado é fechado por
    padrão, e não há default que abra (ADR-0013)."""
    ctx = auth_ctx.get()
    g = await stubs.resource_stub().GrantResource(
        resource_pb2.GrantResourceRequest(
            ctx=call_context_from(ctx),
            resource_id=body.resource_id,
            user_id=body.user_id,
            level=body.level,
        ),
        metadata=core.metadata(),
        timeout=_deadline(),
    )
    return GrantSummary(
        id=g.id, resource_id=g.resource.id, user_id=g.user.id, level=g.level
    )


@log
@account_scoped
@require_role("owner", "admin")
async def revoke_grant(grant_id: str) -> bool:
    ctx = auth_ctx.get()
    resp = await stubs.resource_stub().RevokeGrant(
        resource_pb2.RevokeGrantRequest(ctx=call_context_from(ctx), grant_id=grant_id),
        metadata=core.metadata(),
        timeout=_deadline(),
    )
    return resp.revoked
