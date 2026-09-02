"""Second factor use cases — called by REST and by gRPC, with no duplication.

The rule lives in the CORE (ADR-0027): the seed is in the vault, the code is
checked there, and the step-up is recorded there. What is here is the edge's
work — translating, and refusing early what does not need a round trip.

Two things this module deliberately does NOT do:

  - it does not keep the code, the seed or the challenge. The BFF has no
    database and no secret (ADR-0023), and a cache "just for the challenge"
    would be the second place where a live credential exists;
  - it does not decide who may enrol what. The policy is the account's, read in
    the core: duplicating it here would be the second ruler this platform keeps
    refusing.
"""

from pydantic import BaseModel, Field

from app.coreclient import stubs
from app.coreclient.client import core
from app.coreclient.convert import call_context_from
from app.coreclient.gen.dop.v1 import secondfactor_pb2
from app.platform.context import auth_ctx
from app.platform.logging.decorator import log
from app.settings import settings


def _deadline() -> float:
    return settings.core_deadline_s


# The wire's vocabulary, in the edge's language. The names repeat the domain's
# — a parallel dictionary is where a translation bug between the two ends is
# born.
_KIND_TO_PROTO = {
    "totp": secondfactor_pb2.SECOND_FACTOR_KIND_TOTP,
    "email": secondfactor_pb2.SECOND_FACTOR_KIND_EMAIL,
    "sms": secondfactor_pb2.SECOND_FACTOR_KIND_SMS,
}
_KIND_FROM_PROTO = {v: k for k, v in _KIND_TO_PROTO.items()}
_STATUS_FROM_PROTO = {
    secondfactor_pb2.SecondFactor.STATUS_PENDING: "pending",
    secondfactor_pb2.SecondFactor.STATUS_ACTIVE: "active",
    secondfactor_pb2.SecondFactor.STATUS_REVOKED: "revoked",
}

KINDS = tuple(_KIND_TO_PROTO)


# ── the edge's models ───────────────────────────────────────────────────────


class FactorSummary(BaseModel):
    id: str
    kind: str
    status: str
    label: str
    # Always masked. The whole value never crosses the edge.
    masked_destination: str = ""
    confirmed_at: str | None = None
    last_used_at: str | None = None


class NewFactor(BaseModel):
    kind: str
    label: str = Field(min_length=1, max_length=60)
    # Required for email and sms, refused for totp. The core is what refuses —
    # the edge only stops what costs a round trip to discover.
    destination: str = ""


class EnrollResponse(BaseModel):
    factor: FactorSummary
    challenge_id: str
    # Present ONLY for TOTP, and only in this response.
    secret: str = ""
    uri: str = ""


class ConfirmFactor(BaseModel):
    challenge_id: str
    code: str = Field(min_length=1, max_length=32)


class ConfirmResponse(BaseModel):
    factor: FactorSummary | None = None
    # Present only on the FIRST active factor.
    recovery_codes: list[str] = Field(default_factory=list)


class ChallengeRequest(BaseModel):
    # Empty uses the person's only active factor; with more than one the core
    # refuses, because choosing would send an SMS — and a charge — to somebody
    # who wanted TOTP.
    factor_id: str = ""


class ChallengeResponse(BaseModel):
    challenge_id: str
    kind: str
    masked_destination: str = ""
    expires_at: str | None = None


class VerifyRequest(BaseModel):
    challenge_id: str
    code: str = Field(min_length=1, max_length=32)


class RecoveryRequest(BaseModel):
    code: str = Field(min_length=1, max_length=64)


class StepUpResponse(BaseModel):
    method: str
    recovery: bool = False
    expires_at: str | None = None


class SecondFactorState(BaseModel):
    required: bool
    enrolled: bool
    stepped_up: bool
    needs_setup: bool
    allowed: list[str]
    factors: list[FactorSummary]
    recovery_codes_left: int
    step_up_expires_at: str | None = None


class RecoveryCodes(BaseModel):
    codes: list[str]


# ── conversion ──────────────────────────────────────────────────────────────


def _ts(value) -> str | None:
    """A Timestamp that was never set comes back as None, not as 1970.

    A zeroed date on the screen reads as "confirmed in 1970", and a wrong date
    is worse than no date (the same rule as the demand's stages).
    """
    return value.ToDatetime().isoformat() + "Z" if value.seconds or value.nanos else None


def _factor(f) -> FactorSummary:
    return FactorSummary(
        id=f.id,
        kind=_KIND_FROM_PROTO.get(f.kind, ""),
        status=_STATUS_FROM_PROTO.get(f.status, ""),
        label=f.label,
        masked_destination=f.masked_destination,
        confirmed_at=_ts(f.confirmed_at),
        last_used_at=_ts(f.last_used_at),
    )


def _kind_value(kind: str) -> int:
    """An unknown kind is refused HERE, with no round trip.

    It is the one policy decision at the edge, and it is not a policy: it is a
    typo. Which kinds the ACCOUNT accepts is the core's answer.
    """
    if kind not in _KIND_TO_PROTO:
        from fastapi import HTTPException

        raise HTTPException(
            status_code=422, detail=f"unknown second factor kind: {kind!r}"
        )
    return _KIND_TO_PROTO[kind]


# ── use cases ───────────────────────────────────────────────────────────────


@log
async def state() -> SecondFactorState:
    """What the cockpit needs in order to decide the screen.

    It is ONE call on purpose: whether the account requires a factor, whether
    the person has one and whether this session has answered are three answers
    that only make sense together — asking for them separately is how a screen
    ends up showing "set up your second factor" to somebody who already has one.
    """
    ctx = auth_ctx.get()
    resp = await stubs.second_factor_stub().GetSecondFactorState(
        secondfactor_pb2.GetSecondFactorStateRequest(ctx=call_context_from(ctx)),
        metadata=core.metadata(),
        timeout=_deadline(),
    )
    return SecondFactorState(
        required=resp.required,
        enrolled=resp.enrolled,
        stepped_up=resp.stepped_up,
        needs_setup=resp.needs_setup,
        allowed=[_KIND_FROM_PROTO.get(k, "") for k in resp.allowed],
        factors=[_factor(f) for f in resp.factors],
        recovery_codes_left=resp.recovery_codes_left,
        step_up_expires_at=_ts(resp.step_up_expires_at),
    )


@log
async def list_factors() -> list[FactorSummary]:
    ctx = auth_ctx.get()
    resp = await stubs.second_factor_stub().ListSecondFactors(
        secondfactor_pb2.ListSecondFactorsRequest(ctx=call_context_from(ctx)),
        metadata=core.metadata(),
        timeout=_deadline(),
    )
    return [_factor(f) for f in resp.factors]


@log(mask=["secret", "uri"])
async def enroll(body: NewFactor) -> EnrollResponse:
    """Registers a factor. It is born PENDING — what activates it is `confirm`.

    The `secret` and the `uri` are masked in the log: they are the seed, and a
    seed in a log is a factor anybody who reads the log can clone.
    """
    ctx = auth_ctx.get()
    resp = await stubs.second_factor_stub().EnrollSecondFactor(
        secondfactor_pb2.EnrollSecondFactorRequest(
            ctx=call_context_from(ctx),
            kind=_kind_value(body.kind),
            label=body.label,
            destination=body.destination,
        ),
        metadata=core.metadata(),
        timeout=_deadline(),
    )
    return EnrollResponse(
        factor=_factor(resp.factor),
        challenge_id=resp.challenge_id,
        secret=resp.secret,
        uri=resp.uri,
    )


@log(mask=["code", "recovery_codes"])
async def confirm(factor_id: str, body: ConfirmFactor) -> ConfirmResponse:
    ctx = auth_ctx.get()
    resp = await stubs.second_factor_stub().ConfirmSecondFactor(
        secondfactor_pb2.ConfirmSecondFactorRequest(
            ctx=call_context_from(ctx),
            factor_id=factor_id,
            challenge_id=body.challenge_id,
            code=body.code,
        ),
        metadata=core.metadata(),
        timeout=_deadline(),
    )
    return ConfirmResponse(
        factor=_factor(resp.factor) if resp.factor.id else None,
        recovery_codes=list(resp.recovery_codes),
    )


@log
async def revoke(factor_id: str) -> None:
    ctx = auth_ctx.get()
    await stubs.second_factor_stub().RevokeSecondFactor(
        secondfactor_pb2.RevokeSecondFactorRequest(
            ctx=call_context_from(ctx), id=factor_id
        ),
        metadata=core.metadata(),
        timeout=_deadline(),
    )


@log
async def challenge(body: ChallengeRequest) -> ChallengeResponse:
    ctx = auth_ctx.get()
    resp = await stubs.second_factor_stub().ChallengeSecondFactor(
        secondfactor_pb2.ChallengeSecondFactorRequest(
            ctx=call_context_from(ctx), factor_id=body.factor_id
        ),
        metadata=core.metadata(),
        timeout=_deadline(),
    )
    return ChallengeResponse(
        challenge_id=resp.challenge_id,
        kind=_KIND_FROM_PROTO.get(resp.kind, ""),
        masked_destination=resp.masked_destination,
        expires_at=_ts(resp.expires_at),
    )


@log(mask=["code"])
async def verify(body: VerifyRequest) -> StepUpResponse:
    ctx = auth_ctx.get()
    resp = await stubs.second_factor_stub().VerifySecondFactor(
        secondfactor_pb2.VerifySecondFactorRequest(
            ctx=call_context_from(ctx),
            challenge_id=body.challenge_id,
            code=body.code,
        ),
        metadata=core.metadata(),
        timeout=_deadline(),
    )
    return _step_up(resp)


@log(mask=["code"])
async def verify_recovery(body: RecoveryRequest) -> StepUpResponse:
    ctx = auth_ctx.get()
    resp = await stubs.second_factor_stub().VerifyRecoveryCode(
        secondfactor_pb2.VerifyRecoveryCodeRequest(
            ctx=call_context_from(ctx), code=body.code
        ),
        metadata=core.metadata(),
        timeout=_deadline(),
    )
    return _step_up(resp)


@log(mask=["codes"])
async def regenerate_recovery_codes() -> RecoveryCodes:
    ctx = auth_ctx.get()
    resp = await stubs.second_factor_stub().RegenerateRecoveryCodes(
        secondfactor_pb2.RegenerateRecoveryCodesRequest(ctx=call_context_from(ctx)),
        metadata=core.metadata(),
        timeout=_deadline(),
    )
    return RecoveryCodes(codes=list(resp.codes))


def _step_up(resp) -> StepUpResponse:
    return StepUpResponse(
        method="recovery_code" if resp.recovery else _KIND_FROM_PROTO.get(resp.method, ""),
        recovery=resp.recovery,
        expires_at=_ts(resp.expires_at),
    )
