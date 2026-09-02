"""Resource use cases — integrations, skills, flows.

The same discipline as `identity` and `hierarchy`: the rule lives here, the
router and the servicer translate.

**This module's hard rule: the secret never comes back.** `SetCredential` writes
it into the vault and returns only an opaque reference; there is no path through
the edge that reads the value, and there must not come to be one. The one that
needs the secret is the executor, straight from the vault (ADR-0001). That is
why the value also goes into no log and no error message — the logger's
automatic masking covers the field, but the first line of defence is not passing
the value on.
"""

import base64

from google.protobuf import struct_pb2
from pydantic import BaseModel, Field

from app.coreclient import stubs
from app.coreclient.client import core
from app.coreclient.gen.dop.v1 import resource_pb2
from app.platform.logging.decorator import log
from app.platform.security.decorator import account_scoped, require_role
from app.settings import settings

# Name ↔ enum in one place only. Spreading this table out is how the two ends
# start disagreeing about what a "git_flow" is.
_KIND_BY_NAME = {
    "integration": resource_pb2.Resource.KIND_INTEGRATION,
    "skill": resource_pb2.Resource.KIND_SKILL,
    "workflow": resource_pb2.Resource.KIND_WORKFLOW,
    "git_flow": resource_pb2.Resource.KIND_GIT_FLOW,
}
_NAME_BY_KIND = {v: k for k, v in _KIND_BY_NAME.items()}


def _deadline() -> float:
    return settings.core_deadline_s


def _idempotency(key: str = "") -> str:
    return key or core.idempotency_key()


class ResourceSummary(BaseModel):
    id: str
    kind: str
    name: str
    version: int = 1
    config: dict = Field(default_factory=dict)
    # An opaque label derivable from the row — its presence says there IS a
    # credential, never which one. Empty means an integration with no secret
    # yet.
    credential_ref: str = ""


class NewResource(BaseModel):
    kind: str = Field(min_length=1)
    name: str = Field(min_length=1)
    # The category and the provider go here: the category is a lowercase string
    # (git | task_manager | agent) because the one that rules the vocabulary is
    # the provider, not the platform (ADR-0013).
    config: dict = Field(default_factory=dict)


class NewCredential(BaseModel):
    """The secret arrives in base64 — bytes in the core's contract.

    A separate model on purpose: that way the value never touches the model the
    edge returns, and there is no way for anybody to accidentally serialize it
    back.
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
        config = {k: _value(v) for k, v in r.config.fields.items()}
    return ResourceSummary(
        id=r.id,
        kind=_NAME_BY_KIND.get(r.kind, ""),
        name=r.name,
        version=r.version,
        config=config,
        credential_ref=r.credential_ref,
    )


def _value(v: struct_pb2.Value):
    """Protobuf's Struct into Python, only what the config actually uses."""
    field = v.WhichOneof("kind")
    if field == "string_value":
        return v.string_value
    if field == "number_value":
        return v.number_value
    if field == "bool_value":
        return v.bool_value
    if field == "list_value":
        return [_value(i) for i in v.list_value.values]
    if field == "struct_value":
        return {k: _value(i) for k, i in v.struct_value.fields.items()}
    return None


@log
@account_scoped
async def list_resources(kind: str = "") -> list[ResourceSummary]:
    """The resources visible to the actor.

    The core already FILTERS by what the actor may use — somebody else's
    integration does not appear. The edge does not refilter: duplicating the
    visibility rule here would create a second truth that eventually disagrees
    with the first.
    """
    request = resource_pb2.ListResourcesRequest()
    if kind:
        request.kind = _KIND_BY_NAME.get(kind, resource_pb2.Resource.KIND_UNSPECIFIED)
    resp = await stubs.resource_stub().ListResources(
        request, metadata=core.metadata(), timeout=_deadline()
    )
    return [_resource(r) for r in resp.resources]


@log
@account_scoped
async def get_resource(resource_id: str) -> ResourceSummary:
    r = await stubs.resource_stub().GetResource(
        resource_pb2.GetResourceRequest(id=resource_id),
        metadata=core.metadata(),
        timeout=_deadline(),
    )
    return _resource(r)


@log
@account_scoped
async def create_resource(body: NewResource, idempotency_key: str = "") -> ResourceSummary:
    """Creates the resource WITHOUT a credential — the secret goes in through `set_credential`.

    Separating the two steps is not ceremony: it keeps the value out of the
    request that creates, which is the most logged and most retried of all.
    """
    config = struct_pb2.Struct()
    config.update(body.config)
    r = await stubs.resource_stub().CreateResource(
        resource_pb2.CreateResourceRequest(
            kind=_KIND_BY_NAME.get(body.kind, resource_pb2.Resource.KIND_UNSPECIFIED),
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
    """Writes the secret into the vault and returns the RESOURCE, without the value.

    Returning the resource (and not only the reference) saves the cockpit a
    round trip, since it needs to show "credential configured". The value does
    not come along because there is no path that reads it.
    """
    stub = stubs.resource_stub()
    await stub.SetCredential(
        resource_pb2.SetCredentialRequest(
            resource_id=resource_id,
            secret=base64.b64decode(body.secret_base64),
        ),
        metadata=core.metadata(),
        timeout=_deadline(),
    )
    r = await stub.GetResource(
        resource_pb2.GetResourceRequest(id=resource_id),
        metadata=core.metadata(),
        timeout=_deadline(),
    )
    return _resource(r)


@log
@account_scoped
@require_role("owner", "admin")
async def grant_resource(body: NewGrant) -> GrantSummary:
    """A grant is always explicit — a credentialed resource is closed by
    default, and there is no default that opens it (ADR-0013)."""
    g = await stubs.resource_stub().GrantResource(
        resource_pb2.GrantResourceRequest(
            resource_id=body.resource_id,
            user_id=body.user_id,
            level=body.level,
        ),
        metadata=core.metadata(),
        timeout=_deadline(),
    )
    return GrantSummary(id=g.id, resource_id=g.resource.id, user_id=g.user.id, level=g.level)


@log
@account_scoped
@require_role("owner", "admin")
async def list_member_grants(user_id: str) -> list[GrantSummary]:
    """The grants one person holds in the active account.

    It is the read the members screen needs in order to SHOW access before
    editing it; without it the screen can only write blind.
    """
    resp = await stubs.resource_stub().ListMemberGrants(
        resource_pb2.ListMemberGrantsRequest(user_id=user_id),
        metadata=core.metadata(),
        timeout=_deadline(),
    )
    return [
        GrantSummary(id=g.id, resource_id=g.resource.id, user_id=g.user.id, level=g.level)
        for g in resp.grants
    ]


async def revoke_grant(grant_id: str) -> bool:
    resp = await stubs.resource_stub().RevokeGrant(
        resource_pb2.RevokeGrantRequest(grant_id=grant_id),
        metadata=core.metadata(),
        timeout=_deadline(),
    )
    return resp.revoked
