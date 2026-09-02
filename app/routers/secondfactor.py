"""REST for the second factor (ADR-0027).

Note which routes are NOT here: there is no reading of the seed, no reading of a
code, and no route that manages somebody else's factor. The three absences are
the design, not an omission — see the use case's header.
"""

from fastapi import APIRouter, Response

from app.usecases import secondfactor as uc
from app.usecases.secondfactor import (
    ChallengeRequest,
    ChallengeResponse,
    ConfirmFactor,
    ConfirmResponse,
    EnrollResponse,
    FactorSummary,
    NewFactor,
    RecoveryCodes,
    RecoveryRequest,
    SecondFactorState,
    StepUpResponse,
    VerifyRequest,
)

__all__ = ["router"]

router = APIRouter(prefix="/api/v1", tags=["second factor"])


@router.get("/me/second-factor", response_model=SecondFactorState)
async def second_factor_state() -> SecondFactorState:
    """Whether the account requires it, whether the person has it, whether this session answered."""
    return await uc.state()


@router.get("/me/second-factor/factors", response_model=list[FactorSummary])
async def list_second_factors() -> list[FactorSummary]:
    """The caller's factors, with the destination always masked."""
    return await uc.list_factors()


@router.post("/me/second-factor/factors", response_model=EnrollResponse, status_code=201)
async def enroll_second_factor(body: NewFactor) -> EnrollResponse:
    """Registers a factor. It is born PENDING: what activates it is the confirmation."""
    return await uc.enroll(body)


@router.post(
    "/me/second-factor/factors/{factor_id}/confirm", response_model=ConfirmResponse
)
async def confirm_second_factor(factor_id: str, body: ConfirmFactor) -> ConfirmResponse:
    """Proves possession and activates. On the FIRST factor it returns the recovery codes."""
    return await uc.confirm(factor_id, body)


@router.delete("/me/second-factor/factors/{factor_id}", status_code=204)
async def revoke_second_factor(factor_id: str) -> Response:
    """Removes a factor. The last one is refused where the account requires it."""
    await uc.revoke(factor_id)
    return Response(status_code=204)


@router.post("/me/second-factor/challenge", response_model=ChallengeResponse)
async def challenge_second_factor(body: ChallengeRequest) -> ChallengeResponse:
    """Starts a step-up. For e-mail and SMS it SENDS the code."""
    return await uc.challenge(body)


@router.post("/me/second-factor/verify", response_model=StepUpResponse)
async def verify_second_factor(body: VerifyRequest) -> StepUpResponse:
    """Answers the challenge and steps THIS session up."""
    return await uc.verify(body)


@router.post("/me/second-factor/recovery", response_model=StepUpResponse)
async def verify_recovery_code(body: RecoveryRequest) -> StepUpResponse:
    """The way back when the factor is lost. The code dies on use."""
    return await uc.verify_recovery(body)


@router.post("/me/second-factor/recovery-codes", response_model=RecoveryCodes)
async def regenerate_recovery_codes() -> RecoveryCodes:
    """Replaces every recovery code. It requires a stepped-up session."""
    return await uc.regenerate_recovery_codes()
