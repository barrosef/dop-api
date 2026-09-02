"""Delivery use cases — the PR, the merge queue and the techlead's directives.

The same discipline as `identity` and `hierarchy`: the rule lives here and only
here; `app/routers/delivery.py` translates HTTP and `app/grpcapi/delivery.py`
translates protobuf, both calling these SAME functions, with the decorators in
the use case (see `app/usecases/identity.py`'s docstring).

What this module adds to the core:

1. **The refusal for want of green, item by item.** The core enforces ADR-0007
   at the queue's door and refuses with FAILED_PRECONDITION plus a sentence that
   LISTS what is missing ("no approved acceptance run for commit abc1234
   (ADR-0007 §1); the critic's verdict is missing…"). That list is the useful
   part of the answer — it is the recipe for what to do to get in. Passing it on
   as a string would force every client to split it on semicolons to show a
   list; so it is split ONCE, here, into `MergeRefusal.missing`.

   And the refusal becomes a RESPONSE, not an exception: for the agent asking to
   enter the queue, "not yet, this is missing" is work to do, not a failure
   (ADR-0007 §2). REST returns the same thing with a 412, because there the
   status is part of the response.

2. **The delivery board in one call.** The project's PRs and directives are two
   RPCs in the core and a single screen; `get_board` asks for both in parallel.

3. **`pending_reviews`.** The core returns the reviewers with a raw status;
   whoever orders the attention box wants the NUMBER of those who have not
   spoken yet. Counting it in each client is the same rule written three times.

None of this decides about green: green is derived from the verification runs,
in the core, over a specific commit (ADR-0007/ADR-0008). The edge neither
recomputes nor caches that conclusion — yesterday's green is not now's green,
and a second judge of the same fact is how the two ends start disagreeing.
"""

import asyncio

import grpc
from google.protobuf import struct_pb2
from google.protobuf.json_format import MessageToDict
from grpc.aio import AioRpcError
from pydantic import BaseModel, Field

from app.coreclient import stubs
from app.coreclient.client import core
from app.coreclient.gen.dop.v1 import common_pb2, delivery_pb2
from app.platform.logging.decorator import log
from app.platform.security.decorator import account_scoped, require_role
from app.settings import settings

_STATE_BY_ENUM: dict[int, str] = {
    delivery_pb2.MergeQueueEntry.STATE_QUEUED: "queued",
    delivery_pb2.MergeQueueEntry.STATE_REBASING: "rebasing",
    delivery_pb2.MergeQueueEntry.STATE_VERIFYING: "verifying",
    delivery_pb2.MergeQueueEntry.STATE_MERGED: "merged",
    delivery_pb2.MergeQueueEntry.STATE_CONFLICT: "conflict",
}

_DIRECTIVE_BY_ENUM: dict[int, str] = {
    delivery_pb2.Directive.KIND_CHERRY_PICK: "cherry_pick",
    delivery_pb2.Directive.KIND_MERGE_ORDER: "merge_order",
    delivery_pb2.Directive.KIND_FILE_PARTITION: "file_partition",
    delivery_pb2.Directive.KIND_CROSS_VERIFY: "cross_verify",
}

# How the core builds the refusal's sentence
# (internal/domain/delivery/service.go): "<reason>: <item>; <item>". The first
# ": " separates the reason from the items.
_REASON_SEP = ": "
_ITEM_SEP = "; "

# A reviewer who has not spoken yet. The vocabulary is the git provider's and
# travels raw (dop.v1 uses a string here); the edge only knows how to recognize
# the pending one.
_PENDING_REVIEW = "pending"


def _deadline() -> float:
    return settings.core_deadline_s


def _idempotency(key: str = "") -> str:
    return key or core.idempotency_key()


# ── the edge's models ───────────────────────────────────────────────────────


class Reviewer(BaseModel):
    name: str = ""
    initials: str = ""
    status: str = ""  # approved | rejected | pending, the provider's


class PullRequest(BaseModel):
    id: str
    demand_id: str = ""
    repo: str = ""
    source_branch: str = ""
    target_branch: str = ""
    url: str = ""
    merged: bool = False
    has_conflict: bool = False
    reviewers: list[Reviewer] = Field(default_factory=list)
    pending_reviews: int = 0


class MergeQueueEntry(BaseModel):
    id: str
    repo_id: str = ""
    demand_id: str = ""
    position: int = 0
    state: str = ""
    overlapping_files: list[str] = Field(default_factory=list)


class Directive(BaseModel):
    id: str
    project_id: str = ""
    kind: str = ""
    payload: dict = Field(default_factory=dict)
    decided_by_id: str = ""
    decided_by_name: str = ""


class MergeRefusal(BaseModel):
    """The queue's refusal, taken apart — see item 1 of the module's docstring."""

    reason: str = ""
    missing: list[str] = Field(default_factory=list)


class MergeAttempt(BaseModel):
    """It got into the queue, or it did not and here is why.

    Exactly one of the two comes filled in. Absent ≠ zeroed: a zeroed entry and a
    refusal are the difference between being and not being in the queue.
    """

    entry: MergeQueueEntry | None = None
    refusal: MergeRefusal | None = None


class DeliveryBoard(BaseModel):
    pull_requests: list[PullRequest] = Field(default_factory=list)
    directives: list[Directive] = Field(default_factory=list)


class NewMergeEntry(BaseModel):
    demand_id: str = Field(min_length=1)


class DirectiveDecision(BaseModel):
    """The decision is a free payload: each directive kind's shape is the
    techlead's (ADR-0015), and typing it here would freeze what is still being
    discovered."""

    decision: dict = Field(default_factory=dict)


# ── translating the core into the edge ──────────────────────────────────────


def _pull_request(pr: delivery_pb2.PullRequest) -> PullRequest:
    reviewers = [Reviewer(name=r.name, initials=r.initials, status=r.status) for r in pr.reviewers]
    return PullRequest(
        id=pr.id,
        demand_id=pr.demand.id,
        repo=pr.repo,
        source_branch=pr.source_branch,
        target_branch=pr.target_branch,
        url=pr.url,
        merged=pr.merged,
        has_conflict=pr.has_conflict,
        reviewers=reviewers,
        pending_reviews=sum(1 for r in reviewers if r.status == _PENDING_REVIEW),
    )


def _entry(e: delivery_pb2.MergeQueueEntry) -> MergeQueueEntry:
    return MergeQueueEntry(
        id=e.id,
        repo_id=e.repo_id,
        demand_id=e.demand.id,
        position=e.position,
        state=_STATE_BY_ENUM.get(e.state, ""),
        overlapping_files=list(e.overlapping_files),
    )


def _directive(d: delivery_pb2.Directive) -> Directive:
    # MessageToDict converts the whole tree into Python types; iterating the
    # Struct by hand would return protobuf submessages that REST's JSON cannot
    # write.
    payload = MessageToDict(d.payload) if d.HasField("payload") else {}
    return Directive(
        id=d.id,
        project_id=d.project.id,
        kind=_DIRECTIVE_BY_ENUM.get(d.kind, ""),
        payload=payload,
        decided_by_id=d.decided_by.id,
        decided_by_name=d.decided_by.name,
    )


def _refusal(detail: str) -> MergeRefusal:
    """Takes the core's refusal sentence apart into a reason + a list of what is missing.

    Tolerant: a refusal with no list (an already merged PR, a demand with no open
    PR) becomes the reason alone, and not an invented list. The text is NEVER
    rewritten — what the core says is what the dev reads, because it is the core
    that knows what happened.
    """
    reason, sep, tail = detail.strip().partition(_REASON_SEP)
    if not sep:
        return MergeRefusal(reason=detail.strip())
    items = [i.strip() for i in tail.split(_ITEM_SEP) if i.strip()]
    return MergeRefusal(reason=reason.strip(), missing=items)


# ── use cases ───────────────────────────────────────────────────────────────


@log
@account_scoped
async def list_pull_requests(demand_id: str = "", project_id: str = "") -> list[PullRequest]:
    """One demand's PRs (the demand's screen) or one project's (the delivery screen)."""
    request = delivery_pb2.ListPullRequestsRequest(demand_id=demand_id)
    if project_id:
        request.project.id = project_id
    resp = await stubs.delivery_stub().ListPullRequests(
        request, metadata=core.metadata(), timeout=_deadline()
    )
    return [_pull_request(pr) for pr in resp.pull_requests]


@log
@account_scoped
async def list_directives(project_id: str) -> list[Directive]:
    resp = await stubs.delivery_stub().ListDirectives(
        delivery_pb2.ListDirectivesRequest(project=common_pb2.ProjectRef(id=project_id)),
        metadata=core.metadata(),
        timeout=_deadline(),
    )
    return [_directive(d) for d in resp.directives]


@log
@account_scoped
async def get_board(project_id: str) -> DeliveryBoard:
    """The project's delivery screen: PRs and directives, in parallel.

    Neither depends on the other, so adding the latencies would turn the
    aggregation into a cost. `gather` propagates the first failure: half a screen
    without saying it is half is worse than an error.
    """
    prs, directives = await asyncio.gather(
        list_pull_requests(project_id=project_id), list_directives(project_id)
    )
    return DeliveryBoard(pull_requests=prs, directives=directives)


@log
@account_scoped
async def get_merge_queue(repo_id: str) -> list[MergeQueueEntry]:
    """ONE repository's queue — there is no "the queue" in the singular (ADR-0008).

    It is the repository that serializes: two PRs in different repositories do
    not invalidate each other, and a global queue would be an invented
    serialization.
    """
    resp = await stubs.delivery_stub().GetMergeQueue(
        delivery_pb2.GetMergeQueueRequest(repo_id=repo_id),
        metadata=core.metadata(),
        timeout=_deadline(),
    )
    return [_entry(e) for e in resp.entries]


@log
@account_scoped
@require_role("owner", "admin", "developer")
async def enqueue_merge(
    repo_id: str, body: NewMergeEntry, idempotency_key: str = ""
) -> MergeAttempt:
    """Asks to enter the repository's queue — and returns the refusal IN FULL.

    FAILED_PRECONDITION here is neither the caller's failure nor the system's: it
    is the core saying "the commit is not green yet, and here is what is
    missing". It becomes a response with the list intact. Any OTHER status keeps
    going up to the usual translators (`grpc_exception_handler` in REST,
    `ErrorInterceptor` in gRPC) — not least because only they know how to write a
    5xx without leaking the core's detail. A broad `except` here would swallow
    NOT_FOUND as if it were a refusal.
    """
    try:
        e = await stubs.delivery_stub().EnqueueMerge(
            delivery_pb2.EnqueueMergeRequest(
                repo_id=repo_id,
                demand_id=body.demand_id,
                idempotency_key=_idempotency(idempotency_key),
            ),
            metadata=core.metadata(),
            timeout=_deadline(),
        )
    except AioRpcError as exc:
        if exc.code() is not grpc.StatusCode.FAILED_PRECONDITION:
            raise
        return MergeAttempt(refusal=_refusal(exc.details() or ""))
    return MergeAttempt(entry=_entry(e))


@log
@account_scoped
@require_role("owner", "admin", "developer")
async def decide_directive(
    directive_id: str, body: DirectiveDecision, idempotency_key: str = ""
) -> Directive:
    """The coordination decision is the DEV's (ADR-0015) — the techlead recommends."""
    decision = struct_pb2.Struct()
    decision.update(body.decision)
    d = await stubs.delivery_stub().DecideDirective(
        delivery_pb2.DecideDirectiveRequest(
            directive_id=directive_id,
            decision=decision,
            idempotency_key=_idempotency(idempotency_key),
        ),
        metadata=core.metadata(),
        timeout=_deadline(),
    )
    return _directive(d)
