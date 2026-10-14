"""The onboarding journey at the edge (spec 2026-09-20 §5).

What matters here is the SHAPE the core receives and the parity between REST
and gRPC: every rule (what may be skipped, what "done" needs, what complete
refuses) lives in the core and is tested there. The edge proves it forwards
the right request, translates the refusal, and never invents a decision.
"""

import grpc
import pytest

from app.coreclient.gen.dop.v1 import identity_pb2
from app.grpcapi.gen.dop.bff.v1 import identity_pb2 as bff
from tests.conftest import metadata_for, token_for

# The journey needs a session, not an account: no x-account-id on purpose.
SESSION = {"authorization": token_for()}
SESSION_MD = metadata_for(token_for(), account_id="")


class TestProfile:
    def test_me_reads_the_cores_row(self, client, core):
        core.user.name = "Ed"
        core.user.phone = "+5511999999999"
        core.user.onboarded = True
        r = client.get("/api/v1/me", headers=SESSION)
        assert r.status_code == 200
        body = r.json()
        assert body["name"] == "Ed"
        assert body["phone"] == "+5511999999999"
        assert body["onboarded"] is True
        assert core.GetUser.last["request"].id == "u-1"

    def test_patch_forwards_only_what_arrived(self, client, core):
        r = client.patch("/api/v1/me", headers=SESSION, json={"name": "Ed", "phone": ""})
        assert r.status_code == 200
        req = core.UpdateProfile.last["request"]
        assert req.HasField("name") and req.name == "Ed"
        assert req.HasField("phone") and req.phone == ""
        assert not req.HasField("locale"), "an absent field must stay untouched"
        assert not req.HasField("birth_date")
        assert not req.HasField("phone_whatsapp")

    def test_patch_carries_the_whatsapp_flag_as_a_tristate(self, client, core):
        r = client.patch("/api/v1/me", headers=SESSION, json={"phone_whatsapp": False})
        assert r.status_code == 200
        req = core.UpdateProfile.last["request"]
        assert req.HasField("phone_whatsapp") and req.phone_whatsapp is False

    def test_the_cores_refusal_crosses_as_a_400(self, client, core):
        core.UpdateProfile.fails_with(grpc.StatusCode.INVALID_ARGUMENT, "invalid phone")
        r = client.patch("/api/v1/me", headers=SESSION, json={"phone": "11999"})
        assert r.status_code == 400
        assert "invalid phone" in r.json()["detail"]


class TestJourney:
    def test_state_lists_every_step(self, client, core):
        r = client.get("/api/v1/me/onboarding", headers=SESSION)
        assert r.status_code == 200
        body = r.json()
        assert [s["step"] for s in body["steps"]] == ["profile", "contact", "code", "tasks", "plan"]
        assert body["current"] == "profile"
        assert body["complete"] is False
        assert body["personal_account_id"] == "acct-personal"

    def test_record_step_forwards_step_and_status(self, client, core):
        r = client.post(
            "/api/v1/me/onboarding/steps/contact", headers=SESSION, json={"status": "skipped"}
        )
        assert r.status_code == 200
        req = core.RecordOnboardingStep.last["request"]
        assert (req.step, req.status) == ("contact", "skipped")

    def test_a_status_outside_the_vocabulary_never_reaches_the_core(self, client, core):
        r = client.post(
            "/api/v1/me/onboarding/steps/contact", headers=SESSION, json={"status": "maybe"}
        )
        assert r.status_code == 422
        assert core.RecordOnboardingStep.calls == []

    def test_complete_refused_by_the_core_is_a_412(self, client, core):
        core.CompleteOnboarding.fails_with(
            grpc.StatusCode.FAILED_PRECONDITION, "the plan step is not done yet"
        )
        r = client.post("/api/v1/me/onboarding/complete", headers=SESSION)
        assert r.status_code == 412
        assert "plan step" in r.json()["detail"]

    def test_complete_returns_the_state(self, client, core):
        core.onboarding.complete = True
        core.onboarding.current = ""
        r = client.post("/api/v1/me/onboarding/complete", headers=SESSION)
        assert r.status_code == 200
        assert r.json()["complete"] is True


class TestPersonalAccount:
    def test_handle_availability(self, client, core):
        core.CheckHandle.returns(
            identity_pb2.HandleAvailability(handle="dev", available=False, suggestion="dev-x1y2")
        )
        r = client.get("/api/v1/accounts/handles/DEV/availability", headers=SESSION)
        assert r.status_code == 200
        assert r.json() == {"handle": "dev", "available": False, "suggestion": "dev-x1y2"}
        assert core.CheckHandle.last["request"].handle == "DEV"

    def test_patch_current_edits_the_personal_account(self, client, core):
        r = client.patch("/api/v1/accounts/current", headers=SESSION, json={"handle": "ed-barros"})
        assert r.status_code == 200
        req = core.UpdatePersonalAccount.last["request"]
        assert req.handle == "ed-barros" and req.display_name == ""
        assert r.json()["kind"] == "personal"

    def test_a_taken_handle_is_a_409(self, client, core):
        core.UpdatePersonalAccount.fails_with(grpc.StatusCode.ALREADY_EXISTS, "handle taken")
        r = client.patch("/api/v1/accounts/current", headers=SESSION, json={"handle": "acme"})
        assert r.status_code == 409

    def test_set_plan_records_the_choice(self, client, core):
        r = client.put("/api/v1/accounts/current/plan", headers=SESSION, json={"plan_key": "free"})
        assert r.status_code == 200
        assert core.SetPlan.last["request"].plan_key == "free"
        assert r.json()["plan_key"] == "free"

    def test_an_unknown_plan_is_a_400(self, client, core):
        core.SetPlan.fails_with(grpc.StatusCode.INVALID_ARGUMENT, 'unknown plan: "gold"')
        r = client.put("/api/v1/accounts/current/plan", headers=SESSION, json={"plan_key": "gold"})
        assert r.status_code == 400


class TestParityBetweenTransports:
    """REST and gRPC build the SAME core request — the alarm that fires when
    somebody adds a field to one door and forgets the other."""

    async def test_record_step_builds_the_same_request(self, client, stub_grpc, core):
        client.post("/api/v1/me/onboarding/steps/code", headers=SESSION, json={"status": "done"})
        await stub_grpc.RecordOnboardingStep(
            bff.RecordOnboardingStepRequest(step="code", status="done"), metadata=SESSION_MD
        )
        requests = core.RecordOnboardingStep.requests
        assert len(requests) == 2 and requests[0] == requests[1]

    async def test_set_plan_builds_the_same_request(self, client, stub_grpc, core):
        client.put("/api/v1/accounts/current/plan", headers=SESSION, json={"plan_key": "pro"})
        await stub_grpc.SetPlan(bff.SetPlanRequest(plan_key="pro"), metadata=SESSION_MD)
        requests = core.SetPlan.requests
        assert len(requests) == 2 and requests[0] == requests[1]

    async def test_update_profile_keeps_absence_on_both_ports(self, client, stub_grpc, core):
        client.patch("/api/v1/me", headers=SESSION, json={"name": "Ed"})
        await stub_grpc.UpdateProfile(bff.UpdateProfileRequest(name="Ed"), metadata=SESSION_MD)
        requests = core.UpdateProfile.requests
        assert len(requests) == 2 and requests[0] == requests[1]
        assert not requests[1].HasField("phone")

    async def test_onboarding_state_gives_the_same_result(self, client, stub_grpc, core):
        rest = client.get("/api/v1/me/onboarding", headers=SESSION).json()
        st = await stub_grpc.GetOnboarding(bff.GetOnboardingRequest(), metadata=SESSION_MD)
        assert [(s.step, s.status) for s in st.steps] == [
            (s["step"], s["status"]) for s in rest["steps"]
        ]
        assert st.current == rest["current"]
        assert st.personal_account_id == rest["personal_account_id"]


@pytest.mark.parametrize(
    "method,path",
    [
        ("get", "/api/v1/me/onboarding"),
        ("post", "/api/v1/me/onboarding/complete"),
        ("get", "/api/v1/accounts/handles/dev/availability"),
    ],
)
def test_the_journey_needs_a_session(client, method, path):
    r = getattr(client, method)(path)
    assert r.status_code == 401
