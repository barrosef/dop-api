"""The second factor's gRPC servicer — a protobuf adapter over the use cases.

Symmetric to `app/routers/secondfactor.py`: it receives a message, calls the
SAME function, returns another message. No decision happens here — the rule is
in the core (ADR-0020) and the translation is in the use case.
"""

from google.protobuf.timestamp_pb2 import Timestamp

from app.grpcapi.gen.dop.bff.v1 import secondfactor_pb2 as bff
from app.grpcapi.gen.dop.bff.v1 import secondfactor_pb2_grpc as bff_grpc
from app.usecases import secondfactor as uc

_KIND = {
    "totp": bff.SECOND_FACTOR_KIND_TOTP,
    "email": bff.SECOND_FACTOR_KIND_EMAIL,
    "sms": bff.SECOND_FACTOR_KIND_SMS,
}
_KIND_NAME = {v: k for k, v in _KIND.items()}
_STATUS = {
    "pending": bff.SecondFactorSummary.STATUS_PENDING,
    "active": bff.SecondFactorSummary.STATUS_ACTIVE,
    "revoked": bff.SecondFactorSummary.STATUS_REVOKED,
}


def _ts(value: str | None) -> Timestamp | None:
    """An absent date stays ABSENT on the wire.

    Filling it with zero would say "confirmed in 1970", and the other side's
    HasField would confirm it — the same rule as the demand's stages.
    """
    if not value:
        return None
    from datetime import datetime

    ts = Timestamp()
    ts.FromDatetime(datetime.fromisoformat(value.replace("Z", "")))
    return ts


def _factor(f: uc.FactorSummary) -> bff.SecondFactorSummary:
    msg = bff.SecondFactorSummary(
        id=f.id,
        kind=_KIND.get(f.kind, bff.SECOND_FACTOR_KIND_UNSPECIFIED),
        status=_STATUS.get(f.status, bff.SecondFactorSummary.STATUS_UNSPECIFIED),
        label=f.label,
        masked_destination=f.masked_destination,
    )
    if ts := _ts(f.confirmed_at):
        msg.confirmed_at.CopyFrom(ts)
    if ts := _ts(f.last_used_at):
        msg.last_used_at.CopyFrom(ts)
    return msg


class SecondFactorServicer(bff_grpc.SecondFactorServiceServicer):
    async def GetSecondFactorState(
        self, request, context
    ) -> bff.GetSecondFactorStateResponse:
        st = await uc.state()
        msg = bff.GetSecondFactorStateResponse(
            required=st.required,
            enrolled=st.enrolled,
            stepped_up=st.stepped_up,
            needs_setup=st.needs_setup,
            allowed=[_KIND[k] for k in st.allowed if k in _KIND],
            factors=[_factor(f) for f in st.factors],
            recovery_codes_left=st.recovery_codes_left,
        )
        if ts := _ts(st.step_up_expires_at):
            msg.step_up_expires_at.CopyFrom(ts)
        return msg

    async def ListSecondFactors(self, request, context) -> bff.ListSecondFactorsResponse:
        return bff.ListSecondFactorsResponse(
            factors=[_factor(f) for f in await uc.list_factors()]
        )

    async def EnrollSecondFactor(self, request, context) -> bff.EnrollSecondFactorResponse:
        r = await uc.enroll(
            uc.NewFactor(
                kind=_KIND_NAME.get(request.kind, ""),
                label=request.label,
                destination=request.destination,
            )
        )
        return bff.EnrollSecondFactorResponse(
            factor=_factor(r.factor),
            challenge_id=r.challenge_id,
            secret=r.secret,
            uri=r.uri,
        )

    async def ConfirmSecondFactor(
        self, request, context
    ) -> bff.ConfirmSecondFactorResponse:
        r = await uc.confirm(
            request.factor_id,
            uc.ConfirmFactor(challenge_id=request.challenge_id, code=request.code),
        )
        msg = bff.ConfirmSecondFactorResponse(recovery_codes=r.recovery_codes)
        if r.factor:
            msg.factor.CopyFrom(_factor(r.factor))
        return msg

    async def RevokeSecondFactor(self, request, context) -> bff.SecondFactorSummary:
        await uc.revoke(request.id)
        return bff.SecondFactorSummary(
            id=request.id, status=bff.SecondFactorSummary.STATUS_REVOKED
        )

    async def ChallengeSecondFactor(
        self, request, context
    ) -> bff.ChallengeSecondFactorResponse:
        r = await uc.challenge(uc.ChallengeRequest(factor_id=request.factor_id))
        msg = bff.ChallengeSecondFactorResponse(
            challenge_id=r.challenge_id,
            kind=_KIND.get(r.kind, bff.SECOND_FACTOR_KIND_UNSPECIFIED),
            masked_destination=r.masked_destination,
        )
        if ts := _ts(r.expires_at):
            msg.expires_at.CopyFrom(ts)
        return msg

    async def VerifySecondFactor(self, request, context) -> bff.StepUpResult:
        return _step_up(
            await uc.verify(
                uc.VerifyRequest(challenge_id=request.challenge_id, code=request.code)
            )
        )

    async def VerifyRecoveryCode(self, request, context) -> bff.StepUpResult:
        return _step_up(await uc.verify_recovery(uc.RecoveryRequest(code=request.code)))

    async def RegenerateRecoveryCodes(
        self, request, context
    ) -> bff.RegenerateRecoveryCodesResponse:
        r = await uc.regenerate_recovery_codes()
        return bff.RegenerateRecoveryCodesResponse(codes=r.codes)


def _step_up(r: uc.StepUpResponse) -> bff.StepUpResult:
    msg = bff.StepUpResult(
        method=_KIND.get(r.method, bff.SECOND_FACTOR_KIND_UNSPECIFIED),
        recovery=r.recovery,
    )
    if ts := _ts(r.expires_at):
        msg.expires_at.CopyFrom(ts)
    return msg
