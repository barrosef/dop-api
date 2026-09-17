"""The second factor at the edge (ADR-0020).

What is tested here is the EDGE, not the rule: the rule lives in the core, and
the double that answers here is what lets us ask "what does the BFF do with what
the core says". Three things are being guarded:

  - the seed and the code do NOT leak in a response where they have no business
    (the listing, the state);
  - the destination always comes MASKED;
  - and an unknown kind costs no round trip.
"""

import grpc
import pytest
from fastapi.testclient import TestClient
from google.protobuf import timestamp_pb2

from app.coreclient import stubs
from app.coreclient.gen.dop.v1 import secondfactor_pb2 as sf
from app.coreclient.resolver import CoreResolver
from app.grpcapi.gen.dop.bff.v1 import secondfactor_pb2 as bff
from app.grpcapi.gen.dop.bff.v1 import secondfactor_pb2_grpc as bff_2fa_grpc
from app.grpcapi.interceptors import AuthInterceptor, ErrorInterceptor, LoggingInterceptor
from app.grpcapi.secondfactor import SecondFactorServicer
from app.main import create_app
from app.platform.security.firebase import FirebaseVerifier
from tests.conftest import PROJECT, FakeCall, token_for

HEADERS = {"authorization": token_for(), "x-account-id": "acct-1"}

# The seed a TOTP enrolment returns. It appears in ONE response and must not
# appear in any other — the test hunts for this exact string.
SEED = "JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP"


def factor(id_="fac-1", kind=sf.SECOND_FACTOR_KIND_SMS, status=sf.SecondFactor.STATUS_ACTIVE):
    f = sf.SecondFactor(
        id=id_,
        kind=kind,
        status=status,
        label="iPhone",
        # The core is what masks it: the edge repeats what it received, and
        # masking here would put a security rule in the translator, which is
        # where nobody looks for it.
        masked_destination="*********9999",
    )
    ts = timestamp_pb2.Timestamp()
    ts.FromSeconds(1_756_800_000)
    f.confirmed_at.CopyFrom(ts)
    return f


class FakeSecondFactor:
    """The core's SecondFactorService, with the RPCs the edge uses."""

    def __init__(self):
        self.GetSecondFactorState = FakeCall(
            sf.GetSecondFactorStateResponse(
                required=True,
                enrolled=True,
                stepped_up=False,
                needs_setup=False,
                allowed=[sf.SECOND_FACTOR_KIND_TOTP, sf.SECOND_FACTOR_KIND_EMAIL],
                factors=[factor()],
                recovery_codes_left=8,
            )
        )
        self.ListSecondFactors = FakeCall(
            sf.ListSecondFactorsResponse(factors=[factor()])
        )
        self.EnrollSecondFactor = FakeCall(
            sf.EnrollSecondFactorResponse(
                factor=factor("fac-2", sf.SECOND_FACTOR_KIND_TOTP, sf.SecondFactor.STATUS_PENDING),
                challenge_id="chl-1",
                secret=SEED,
                uri=f"otpauth://totp/DOP:dev@dop.local?secret={SEED}&issuer=DOP",
            )
        )
        self.ConfirmSecondFactor = FakeCall(
            sf.ConfirmSecondFactorResponse(
                factor=factor("fac-2", sf.SECOND_FACTOR_KIND_TOTP),
                recovery_codes=["AAAAAAAA-BBBBBBBB", "CCCCCCCC-DDDDDDDD"],
            )
        )
        self.RevokeSecondFactor = FakeCall(sf.SecondFactor(id="fac-1"))
        self.ChallengeSecondFactor = FakeCall(
            sf.ChallengeSecondFactorResponse(
                challenge_id="chl-9",
                kind=sf.SECOND_FACTOR_KIND_SMS,
                masked_destination="*********9999",
            )
        )
        self.VerifySecondFactor = FakeCall(sf.StepUp(method=sf.SECOND_FACTOR_KIND_SMS))
        self.VerifyRecoveryCode = FakeCall(sf.StepUp(recovery=True))
        self.RegenerateRecoveryCodes = FakeCall(
            sf.RegenerateRecoveryCodesResponse(codes=["EEEEEEEE-FFFFFFFF"])
        )


@pytest.fixture
def two_factor(core, monkeypatch):
    fake = FakeSecondFactor()
    monkeypatch.setattr(stubs, "second_factor_stub", lambda: fake)
    return fake


@pytest.fixture
def client_2fa(two_factor):
    with TestClient(create_app()) as c:
        yield c


@pytest.fixture
async def stub_2fa(two_factor):
    """A real gRPC server, on an ephemeral port, with this domain's servicer."""
    server = grpc.aio.server(
        interceptors=(
            LoggingInterceptor(),
            ErrorInterceptor(),
            AuthInterceptor(FirebaseVerifier(PROJECT), CoreResolver()),
        )
    )
    bff_2fa_grpc.add_SecondFactorServiceServicer_to_server(SecondFactorServicer(), server)
    port = server.add_insecure_port("127.0.0.1:0")
    await server.start()
    try:
        async with grpc.aio.insecure_channel(f"127.0.0.1:{port}") as channel:
            yield bff_2fa_grpc.SecondFactorServiceStub(channel)
    finally:
        await server.stop(0)


class TestTheStateIsOneCall:
    def test_it_answers_the_three_questions_together(self, client_2fa):
        """Whether the account requires it, whether the person has it and whether
        this session answered only make sense together — asking separately is how
        a screen shows 'set up your second factor' to somebody who already has one.
        """
        r = client_2fa.get("/api/v1/me/second-factor", headers=HEADERS)
        assert r.status_code == 200
        body = r.json()
        assert body["required"] is True
        assert body["enrolled"] is True
        assert body["stepped_up"] is False
        assert body["recovery_codes_left"] == 8

    def test_the_allowed_kinds_come_from_the_account(self, client_2fa):
        """The policy is the account's, read in the core. SMS is not in the list,
        and the cockpit is what stops offering it."""
        body = client_2fa.get("/api/v1/me/second-factor", headers=HEADERS).json()
        assert body["allowed"] == ["totp", "email"]

    def test_a_date_that_was_never_set_is_null_not_1970(self, client_2fa):
        body = client_2fa.get("/api/v1/me/second-factor", headers=HEADERS).json()
        assert body["step_up_expires_at"] is None
        assert body["factors"][0]["last_used_at"] is None
        assert body["factors"][0]["confirmed_at"] is not None


class TestWhatMustNotLeak:
    def test_the_destination_is_always_masked(self, client_2fa):
        body = client_2fa.get("/api/v1/me/second-factor/factors", headers=HEADERS).json()
        assert body[0]["masked_destination"] == "*********9999"
        # The whole number never crosses the edge — looked for in the WHOLE
        # serialized body, and not field by field, because checking field by
        # field only catches the leak somebody remembered to imagine.
        raw = client_2fa.get("/api/v1/me/second-factor/factors", headers=HEADERS).text
        assert "999999999" not in raw

    def test_the_seed_appears_in_the_enrolment_and_nowhere_else(self, client_2fa):
        """It is the only moment the seed exists outside the vault.

        An endpoint that gave it back would turn every session into an enrolment
        of a new device.
        """
        enrolled = client_2fa.post(
            "/api/v1/me/second-factor/factors",
            headers=HEADERS,
            json={"kind": "totp", "label": "iPhone"},
        )
        assert enrolled.status_code == 201
        assert enrolled.json()["secret"] == SEED

        for path in ("/api/v1/me/second-factor", "/api/v1/me/second-factor/factors"):
            assert SEED not in client_2fa.get(path, headers=HEADERS).text

    def test_the_recovery_codes_come_only_on_confirmation(self, client_2fa):
        r = client_2fa.post(
            "/api/v1/me/second-factor/factors/fac-2/confirm",
            headers=HEADERS,
            json={"challenge_id": "chl-1", "code": "123456"},
        )
        assert r.status_code == 200
        assert len(r.json()["recovery_codes"]) == 2
        # And they are not readable in any listing afterwards: what is stored is
        # the hash, and the platform cannot show them again.
        assert "AAAAAAAA-BBBBBBBB" not in client_2fa.get(
            "/api/v1/me/second-factor", headers=HEADERS
        ).text


class TestTheEdgeRefusesEarly:
    def test_an_unknown_kind_is_a_422_and_the_core_is_not_called(self, client_2fa, two_factor):
        """It is the one decision at the edge, and it is not a policy: it is a
        typo. WHICH kinds the account accepts is the core's answer."""
        r = client_2fa.post(
            "/api/v1/me/second-factor/factors",
            headers=HEADERS,
            json={"kind": "carrier-pigeon", "label": "x"},
        )
        assert r.status_code == 422
        assert two_factor.EnrollSecondFactor.calls == []

    def test_an_empty_label_does_not_reach_the_core(self, client_2fa, two_factor):
        r = client_2fa.post(
            "/api/v1/me/second-factor/factors",
            headers=HEADERS,
            json={"kind": "totp", "label": ""},
        )
        assert r.status_code == 422
        assert two_factor.EnrollSecondFactor.calls == []


class TestTheChallengeSaysWhereTheCodeWent:
    def test_it_returns_the_kind_and_the_masked_destination(self, client_2fa):
        """'Check your e-mail' and 'check your phone' are different screens, and
        guessing is what makes people wait for a message that is not coming."""
        r = client_2fa.post(
            "/api/v1/me/second-factor/challenge", headers=HEADERS, json={}
        )
        assert r.status_code == 200
        assert r.json()["kind"] == "sms"
        assert r.json()["masked_destination"] == "*********9999"

    def test_a_recovery_code_answers_as_a_recovery_and_not_as_a_kind(self, client_2fa):
        """A recovery code is not a Kind — it is the way back, and the timeline
        has to be able to tell them apart."""
        r = client_2fa.post(
            "/api/v1/me/second-factor/recovery",
            headers=HEADERS,
            json={"code": "AAAAAAAA-BBBBBBBB"},
        )
        assert r.status_code == 200
        assert r.json()["recovery"] is True
        assert r.json()["method"] == "recovery_code"


class TestTheSessionTravelsToTheCore:
    def test_the_step_up_is_per_session_and_the_id_goes_in_the_metadata(
        self, client_2fa, two_factor
    ):
        """Without `x-session-id` the core cannot tell one open session from
        another, and one of them answering would open every door (ADR-0020 §5)."""
        client_2fa.get("/api/v1/me/second-factor", headers=HEADERS)
        md = dict(two_factor.GetSecondFactorState.last["metadata"])
        assert md["x-session-id"]
        # It is DERIVED and opaque: it does not carry the subject in the clear.
        assert "sub-1" not in md["x-session-id"]

    def test_the_same_token_keeps_the_same_session(self, client_2fa, two_factor):
        """It survives the token's refresh — `auth_time` does not change within a
        session — which is what stops the challenge from coming back in the
        middle of the working day."""
        client_2fa.get("/api/v1/me/second-factor", headers=HEADERS)
        first = dict(two_factor.GetSecondFactorState.last["metadata"])["x-session-id"]
        client_2fa.get("/api/v1/me/second-factor", headers=HEADERS)
        second = dict(two_factor.GetSecondFactorState.last["metadata"])["x-session-id"]
        assert first == second


class TestParityBetweenTransports:
    async def test_the_state_is_the_same_on_both_ports(self, stub_2fa, client_2fa):
        """The same use case behind both doors — there is no second
        implementation."""
        rest = client_2fa.get("/api/v1/me/second-factor", headers=HEADERS).json()
        resp = await stub_2fa.GetSecondFactorState(
            bff.GetSecondFactorStateRequest(),
            metadata=(("authorization", token_for()), ("x-account-id", "acct-1")),
        )
        assert resp.required == rest["required"]
        assert resp.enrolled == rest["enrolled"]
        assert resp.recovery_codes_left == rest["recovery_codes_left"]
        assert [f.masked_destination for f in resp.factors] == [
            f["masked_destination"] for f in rest["factors"]
        ]

    async def test_an_absent_date_has_no_field_over_grpc(self, stub_2fa):
        """Absent ≠ zeroed on the way back too: a zeroed Timestamp would say
        'used in 1970', and the other side's HasField would confirm it."""
        resp = await stub_2fa.GetSecondFactorState(
            bff.GetSecondFactorStateRequest(),
            metadata=(("authorization", token_for()), ("x-account-id", "acct-1")),
        )
        assert resp.factors[0].HasField("confirmed_at")
        assert not resp.factors[0].HasField("last_used_at")
        assert not resp.HasField("step_up_expires_at")
