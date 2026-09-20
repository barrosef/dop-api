"""Catalog use cases — the plans and the providers the onboarding journey reads.

Read-only, session required, active account not: the journey asks for these
before the cockpit has picked an account. The rows come from the core; nothing
here decides what a plan or a provider is (spec 2026-09-20 D-4, D-6).
"""

from pydantic import BaseModel, Field

from app.coreclient import stubs
from app.coreclient.client import core
from app.coreclient.gen.dop.v1 import catalog_pb2
from app.platform.logging.decorator import log
from app.settings import settings


def _deadline() -> float:
    return settings.core_deadline_s


class PlanSummary(BaseModel):
    key: str
    name: str
    tagline: str = ""
    features: list[str] = Field(default_factory=list)


class ProviderSummary(BaseModel):
    key: str
    category: str  # git | task_manager
    name: str
    credential_kind: str = "token"
    permissions: list[str] = Field(default_factory=list)
    needs_base_url: bool = False
    # False means "registered, not operated yet": the credential is stored and
    # nothing is done with it. The screen must say so (spec D-4).
    operated: bool = False
    docs_url: str = ""
    brand_color: str = ""


@log
async def plans() -> list[PlanSummary]:
    resp = await stubs.catalog_stub().ListPlans(
        catalog_pb2.ListPlansRequest(), metadata=core.metadata(), timeout=_deadline()
    )
    return [
        PlanSummary(key=p.key, name=p.name, tagline=p.tagline, features=list(p.features))
        for p in resp.plans
    ]


@log
async def providers() -> list[ProviderSummary]:
    resp = await stubs.catalog_stub().ListProviders(
        catalog_pb2.ListProvidersRequest(), metadata=core.metadata(), timeout=_deadline()
    )
    return [
        ProviderSummary(
            key=p.key,
            category=p.category,
            name=p.name,
            credential_kind=p.credential_kind,
            permissions=list(p.permissions),
            needs_base_url=p.needs_base_url,
            operated=p.operated,
            docs_url=p.docs_url,
            brand_color=p.brand_color,
        )
        for p in resp.providers
    ]
