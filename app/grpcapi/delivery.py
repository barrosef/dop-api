"""The delivery gRPC servicer — a protobuf adapter over the use cases.

Symmetric to `app/routers/delivery.py`: it receives a message, calls the SAME
function from `app/usecases/delivery.py`, returns a message. No decisions here.

In particular, the queue's refusal is NOT assembled in this file: it arrives
ready from the use case, with the list of what is missing already split. What
changes between the two ports is only how the refusal travels — here, a field of
the response; in REST, a 412 with the same content in the body.
"""

from google.protobuf import struct_pb2
from google.protobuf.json_format import MessageToDict

from app.grpcapi.gen.dop.bff.v1 import delivery_pb2 as bff
from app.grpcapi.gen.dop.bff.v1 import delivery_pb2_grpc as bff_grpc
from app.usecases import delivery as uc

STATE_BY_ENUM: dict[int, str] = {
    bff.MergeQueueEntry.STATE_QUEUED: "queued",
    bff.MergeQueueEntry.STATE_REBASING: "rebasing",
    bff.MergeQueueEntry.STATE_VERIFYING: "verifying",
    bff.MergeQueueEntry.STATE_MERGED: "merged",
    bff.MergeQueueEntry.STATE_CONFLICT: "conflict",
}
ENUM_BY_STATE = {name: value for value, name in STATE_BY_ENUM.items()}

DIRECTIVE_BY_ENUM: dict[int, str] = {
    bff.Directive.KIND_CHERRY_PICK: "cherry_pick",
    bff.Directive.KIND_MERGE_ORDER: "merge_order",
    bff.Directive.KIND_FILE_PARTITION: "file_partition",
    bff.Directive.KIND_CROSS_VERIFY: "cross_verify",
}
ENUM_BY_DIRECTIVE = {name: value for value, name in DIRECTIVE_BY_ENUM.items()}


def _pull_request(pr: uc.PullRequest) -> bff.PullRequest:
    return bff.PullRequest(
        id=pr.id,
        demand_id=pr.demand_id,
        repo=pr.repo,
        source_branch=pr.source_branch,
        target_branch=pr.target_branch,
        url=pr.url,
        merged=pr.merged,
        has_conflict=pr.has_conflict,
        reviewers=[
            bff.Reviewer(name=r.name, initials=r.initials, status=r.status)
            for r in pr.reviewers
        ],
        pending_reviews=pr.pending_reviews,
    )


def _entry(e: uc.MergeQueueEntry) -> bff.MergeQueueEntry:
    return bff.MergeQueueEntry(
        id=e.id,
        repo_id=e.repo_id,
        demand_id=e.demand_id,
        position=e.position,
        state=ENUM_BY_STATE.get(e.state, bff.MergeQueueEntry.STATE_UNSPECIFIED),
        overlapping_files=e.overlapping_files,
    )


def _directive(d: uc.Directive) -> bff.Directive:
    payload = struct_pb2.Struct()
    payload.update(d.payload)
    return bff.Directive(
        id=d.id,
        project_id=d.project_id,
        kind=ENUM_BY_DIRECTIVE.get(d.kind, bff.Directive.KIND_UNSPECIFIED),
        payload=payload,
        decided_by_id=d.decided_by_id,
        decided_by_name=d.decided_by_name,
    )


class DeliveryServicer(bff_grpc.DeliveryServiceServicer):
    async def GetDeliveryBoard(
        self, request: bff.GetDeliveryBoardRequest, context
    ) -> bff.DeliveryBoard:
        board = await uc.get_board(request.project_id)
        return bff.DeliveryBoard(
            pull_requests=[_pull_request(p) for p in board.pull_requests],
            directives=[_directive(d) for d in board.directives],
        )

    async def ListPullRequests(
        self, request: bff.ListPullRequestsRequest, context
    ) -> bff.ListPullRequestsResponse:
        prs = await uc.list_pull_requests(request.demand_id, request.project_id)
        return bff.ListPullRequestsResponse(
            pull_requests=[_pull_request(p) for p in prs]
        )

    async def GetMergeQueue(
        self, request: bff.GetMergeQueueRequest, context
    ) -> bff.GetMergeQueueResponse:
        queue = await uc.get_merge_queue(request.repo_id)
        return bff.GetMergeQueueResponse(entries=[_entry(e) for e in queue])

    async def EnqueueMerge(
        self, request: bff.EnqueueMergeRequest, context
    ) -> bff.EnqueueMergeResponse:
        attempt = await uc.enqueue_merge(
            request.repo_id,
            uc.NewMergeEntry(demand_id=request.demand_id),
            request.idempotency_key,
        )
        resp = bff.EnqueueMergeResponse()
        # One OR the other, never both zeroed: whoever reads uses HasField to
        # know whether it entered the queue.
        if attempt.entry is not None:
            resp.entry.CopyFrom(_entry(attempt.entry))
        if attempt.refusal is not None:
            resp.refusal.CopyFrom(
                bff.MergeRefusal(
                    reason=attempt.refusal.reason, missing=attempt.refusal.missing
                )
            )
        return resp

    async def ListDirectives(
        self, request: bff.ListDirectivesRequest, context
    ) -> bff.ListDirectivesResponse:
        directives = await uc.list_directives(request.project_id)
        return bff.ListDirectivesResponse(directives=[_directive(d) for d in directives])

    async def DecideDirective(
        self, request: bff.DecideDirectiveRequest, context
    ) -> bff.Directive:
        body = uc.DirectiveDecision(
            decision=MessageToDict(request.decision) if request.HasField("decision") else {}
        )
        return _directive(
            await uc.decide_directive(request.directive_id, body, request.idempotency_key)
        )
