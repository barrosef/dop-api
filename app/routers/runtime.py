"""The turn-running route — an HTTP translation, nothing more.

The runtime does NOT live in the BFF (ADR-0016): this route calls the core,
which is the one that reads the provider's credential, from the vault, in the
same process.
"""

from fastapi import APIRouter, Header

from app.usecases import runtime as uc
from app.usecases.runtime import RunTurn, TurnOutcome

router = APIRouter(prefix="/api/v1", tags=["runtime"])

__all__ = ["RunTurn", "TurnOutcome", "router"]


@router.post("/demands/{demand_id}/threads/{thread_id}/turns", response_model=TurnOutcome)
async def run_turn(
    demand_id: str,
    thread_id: str,
    body: RunTurn,
    idempotency_key: str = Header(default="", alias="Idempotency-Key"),
) -> TurnOutcome:
    """Runs one agent turn on this thread.

    **It answers 200 even when the budget is blown.** ADR-0008 §2's cut is soft:
    the demand pauses and becomes an item in the attention box, and the turn that
    already ran comes back whole — the reply was published on the thread and so
    was the finding. `paused` and `notice` bring what the human needs to decide.

    **Live following does not come out through here.** The published messages
    are events (ADR-0004) and arrive through the SSE that already exists
    (`GET /api/v1/stream/demands/{demand_id}`). This route returns the
    consolidated result; a second streaming path would be a second source of
    truth for the same timeline.

    **`Idempotency-Key` is mandatory**, and the edge does NOT generate one. An
    agent turn spends money: a key invented here would turn a network retry into
    double consumption with nobody asking. It is the client that knows whether
    it is retrying or asking again.

    An unavailable provider, a refused credential and an account with no agent
    integration arrive from the core with the right status, through the usual
    translator — there is no special handling here, and there must not be.
    """
    return await uc.run_turn(demand_id, thread_id, body, idempotency_key)
