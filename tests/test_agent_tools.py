"""The agent tool rules. Every test runs on LayrcakeSession and on AsyncLayrcakeSession."""

from __future__ import annotations

import asyncio
import inspect
import json
import typing
from decimal import Decimal
from typing import Any, AsyncIterator, Callable, Dict, List, Optional, Tuple, Union

import httpx
import pytest
import respx

from layrcake import AsyncLayrcake, Layrcake
from layrcake import agent_tools as tools
from layrcake import types as sdk_types

from .conftest import API_KEY, BASE_URL

Answer = Callable[[Dict[str, Any]], Any]
CAMPAIGN_ID = "4f1c2b8e-0b7a-4c1e-9a55-2f6f1d2c3b4a"
OPERATION_ID = "9b0e6a52-2d4f-4f0a-8f3e-0c1d2e3f4a5b"
APPROVAL_URL = "https://app.layrcake.dev/approvals/a1"
STEPS = [{"subject": "Hi", "body": "Hi {{first_name}}"}]


def meta(charged: str) -> Dict[str, str]:
    return {"request_id": "req_1", "credits_charged": charged, "credits_available": "250"}


LEAD = {
    "fingerprint": "f1",
    "full_name": "Ada Lovelace",
    "title": "Head of Sales",
    "company_name": "Acme",
    "company_domain": "acme.com",
    "work_email": None,
    "linkedin_url": None,
    "location": "London",
}


def _plan(verb: str, estimate: str) -> Dict[str, Any]:
    return {
        "dry_run": True,
        "advisory": True,
        "evaluated_at": "2026-10-20T00:00:00Z",
        "plan": {"verb": verb, "credits_estimate": estimate, "would_exceed": [], "warnings": []},
    }


def _answer(verb: str, estimate: str, real: Answer) -> Answer:
    def answer(body: Dict[str, Any]) -> Any:
        if body.get("dry_run"):
            return {"result": _plan(verb, estimate), "layrcake": meta("0")}
        return real(body)

    return answer


def _verified(body: Dict[str, Any]) -> Dict[str, Any]:
    status = {"ada@acme.com": "valid", "cat@acme.com": "catch_all_valid"}.get(body["email"], "invalid")
    return {"result": {"email": body["email"], "status": status}, "layrcake": meta("1.250000")}


ANSWERS: Dict[str, Answer] = {
    "/v1/find_leads": _answer(
        "find_leads", "3", lambda body: {"result": {"leads": [LEAD], "total_found": 1}, "layrcake": meta("3")}
    ),
    "/v1/enrich": _answer(
        "enrich",
        "5",
        lambda body: {"result": {"contact": {"work_email": "ada@acme.com"}}, "layrcake": meta("5.000000")},
    ),
    "/v1/verify_email": _answer("verify_email", "1.25", _verified),
    "/v1/check_companies": _answer(
        "check_companies",
        "3.5",
        lambda body: {
            "result": {
                "question": body["question"],
                "answered": 1,
                "companies": [
                    {
                        "company_domain": "acme.com",
                        "status": "checked",
                        "answer": "yes",
                        "excerpt": "Book a demo inside the app",
                        "source_url": "https://acme.com/product",
                        "pages_read": ["https://acme.com/product"],
                        "charged": True,
                    }
                ],
            },
            "layrcake": meta("3.5"),
        },
    ),
    "/v1/research_company": _answer(
        "research_company",
        "9",
        lambda body: {
            "result": {"operation_id": OPERATION_ID, "state": "queued", "cached": False, "credits_reserved": "9"},
            "layrcake": meta("0"),
        },
    ),
    f"/v1/operations/{OPERATION_ID}": lambda body: {
        "operation_id": OPERATION_ID,
        "kind": "research_company",
        "state": "succeeded",
        "progress": {},
        "result": {"facts": [{"fact": "Raised a Series B", "source_url": "https://acme.com/news"}]},
        "error": None,
        "created_at": "2026-10-20T00:00:00Z",
        "completed_at": "2026-10-20T00:00:05Z",
    },
    "/v1/campaigns": _answer(
        "create_campaign",
        "0",
        lambda body: {
            "result": {
                "campaign_id": CAMPAIGN_ID,
                "name": body["name"],
                "state": "draft",
                "leads_accepted": len(body["leads"]),
                "leads_suppressed": 0,
                "created_at": "2026-10-20T00:00:00Z",
            }
        },
    ),
    "/v1/launch_campaign": _answer(
        "launch_campaign",
        "0",
        lambda body: {
            "result": {
                "status": "needs_confirmation",
                "campaign_id": body["campaign_id"],
                "confirmation_token": "tok_secret",
                "approval_url": APPROVAL_URL,
                "approval": {"confirmation_token": "tok_secret", "execution": "server"},
                "leads_active": 1,
            }
        },
    ),
    "/v1/me": lambda body: {
        "key": {"id": "k1"},
        "workspace": {"id": "w1", "name": "Test workspace"},
        "guard": {},
        "credits": {"available": "250", "reserved": "0"},
    },
}


class FakeAPI:
    """Answers every request from ANSWERS and records it as (path, body, idempotency key)."""

    def __init__(self, router: respx.MockRouter) -> None:
        self.answers = dict(ANSWERS)
        self.requests: List[Tuple[str, Dict[str, Any], Optional[str]]] = []
        router.route().mock(side_effect=self.handle)

    def handle(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content or b"{}")
        self.requests.append((request.url.path, body, request.headers.get("Idempotency-Key")))
        answer = self.answers.get(request.url.path)
        if answer is None:
            return httpx.Response(404, json={"error": {"code": "not_found", "message": request.url.path}})
        result = answer(body)
        if isinstance(result, httpx.Response):
            return result
        return httpx.Response(200, json=result)

    def paths(self) -> List[str]:
        return [path for path, _, _ in self.requests]


Session = Union[tools.LayrcakeSession, tools.AsyncLayrcakeSession]


class Tools:
    """Drives a sync or an async session the same way, so each test covers both."""

    def __init__(self, kind: str, client: Union[Layrcake, AsyncLayrcake]) -> None:
        self.kind = kind
        self.client = client
        self.session = self.new(budget_credits=100, run_id="run-test", research_timeout=5)

    def new(self, **options: Any) -> Session:
        if isinstance(self.client, AsyncLayrcake):
            return tools.AsyncLayrcakeSession(self.client, **options)
        return tools.LayrcakeSession(self.client, **options)

    async def call(
        self, name: str, arguments: Optional[Dict[str, Any]] = None, session: Optional[Session] = None
    ) -> str:
        output: Any = (session or self.session).call(name, arguments)
        if inspect.isawaitable(output):
            output = await output
        assert isinstance(output, str)
        return output

    async def tool(self, name: str, /, session: Optional[Session] = None, **args: Any) -> Any:
        """Run a tool as a model would and decode its JSON. A refusal comes back as text."""
        output = await self.call(name, args, session)
        try:
            return json.loads(output)
        except ValueError:
            return output


@pytest.fixture
def fake(api: respx.MockRouter) -> FakeAPI:
    return FakeAPI(api)


@pytest.fixture(params=["sync", "async"])
async def t(request: pytest.FixtureRequest, fake: FakeAPI, sleeps: List[float]) -> AsyncIterator[Tools]:
    if request.param == "sync":
        sync_client = Layrcake(api_key=API_KEY, base_url=BASE_URL, max_retries=0)
        sync_client._transport._sleep = sleeps.append
        yield Tools("sync", sync_client)
        sync_client.close()
    else:

        async def record(seconds: float) -> None:
            sleeps.append(seconds)

        async_client = AsyncLayrcake(api_key=API_KEY, base_url=BASE_URL, max_retries=0)
        async_client._transport._sleep = record
        yield Tools("async", async_client)
        await async_client.close()


async def test_paid_calls_carry_the_budget_left_and_an_idempotency_key(t: Tools, fake: FakeAPI) -> None:
    found = await t.tool("find_leads", audience="Sales leaders", titles=["Head of Sales"])
    assert found["leads"][0]["full_name"] == "Ada Lovelace"
    assert "work_email" not in found["leads"][0]
    path, body, key = fake.requests[0]
    assert (path, body["budget_credits"], body["dry_run"], body["limit"]) == ("/v1/find_leads", 100.0, False, 10)
    assert key is not None
    assert key.startswith("run-test-find_leads-")

    await t.tool("enrich", full_name="Ada Lovelace", company_domain="acme.com")
    assert fake.requests[1][1]["want"] == ["work_email"]
    assert fake.requests[1][1]["budget_credits"] == 97.0
    assert t.session.spent == Decimal(8)
    assert t.session.found == 1


async def test_the_same_call_sends_the_same_key(t: Tools, fake: FakeAPI) -> None:
    await t.tool("verify_email", email="ada@acme.com")
    await t.tool("verify_email", email="ada@acme.com")
    assert fake.requests[0][2] == fake.requests[1][2]


def test_keys_match_the_integrations_0_1_keys() -> None:
    """A run resumed with the same run_id after an upgrade must not pay twice, so keys never change."""
    session = tools.LayrcakeSession(api_key=API_KEY, run_id="r")
    assert session.key("verify_email", {"email": "ada@acme.com"}) == "r-verify_email-b21b7c31135d032a2e91775c"
    session.dry_run = True
    key = session.key("create_campaign", ["Q4", [{"email": "ada@acme.com"}], [{"body": "Hi"}]])
    assert key == "r-create_campaign-3940e95c99b917cc70d20631"


async def test_find_leads_needs_a_filter(t: Tools, fake: FakeAPI) -> None:
    output = await t.tool("find_leads", audience="Anyone at all")
    assert output.startswith("Refused: Add at least one of titles")
    assert fake.requests == []


async def test_campaigns_only_take_verified_emails(t: Tools) -> None:
    await t.tool("verify_email", email="ada@acme.com")
    await t.tool("verify_email", email="cat@acme.com")
    bob = await t.tool("verify_email", email="bob@acme.com")
    assert bob["keep"] is False
    assert (t.session.verified, t.session.catch_all) == ({"ada@acme.com", "cat@acme.com"}, {"cat@acme.com"})
    refused = await t.tool("create_campaign", name="Q4", leads=[{"email": "bob@acme.com"}], steps=STEPS)
    assert refused.startswith("Refused: Verify these first")
    assert "bob@acme.com" in refused
    created = await t.tool("create_campaign", name="Q4", leads=[{"email": "Ada@acme.com"}], steps=STEPS)
    assert created["campaign_id"] == CAMPAIGN_ID
    assert t.session.spent == Decimal("3.75")


async def test_require_verified_can_be_turned_off(t: Tools) -> None:
    session = t.new(require_verified=False)
    created = await t.tool("create_campaign", session=session, name="Q4", leads=[{"email": "x@acme.com"}], steps=STEPS)
    assert created["state"] == "draft"


async def test_launch_stops_at_the_approval_link_and_hides_the_token(t: Tools, fake: FakeAPI) -> None:
    preview = await t.tool("launch_campaign", campaign_id=CAMPAIGN_ID)
    assert preview["approval_url"] == APPROVAL_URL
    assert preview["status"] == "needs_confirmation"
    assert "tok_secret" not in json.dumps(preview)
    assert "confirmation_token" not in fake.requests[0][1]
    assert t.session.approval_url == APPROVAL_URL
    assert "waiting for approval" in await t.tool("verify_email", email="x@acme.com")
    assert len(fake.requests) == 1


@pytest.mark.parametrize("session_class", [tools.LayrcakeSession, tools.AsyncLayrcakeSession])
def test_launch_cannot_take_a_confirmation_token(session_class: Any) -> None:
    assert "confirmation_token" not in tools.LaunchCampaignArgs.model_fields
    assert list(inspect.signature(session_class.launch_campaign).parameters) == ["self", "args"]


async def test_a_refused_launch_keeps_the_reason(t: Tools, fake: FakeAPI) -> None:
    fake.answers["/v1/launch_campaign"] = lambda body: httpx.Response(
        409, json={"error": {"code": "no_mailboxes", "message": "Set up sending first."}}
    )
    output = await t.tool("launch_campaign", campaign_id=CAMPAIGN_ID)
    assert output.startswith("Refused: no_mailboxes: Set up sending first.")
    assert t.session.launch_error == "no_mailboxes: Set up sending first."
    assert t.session.approval_url is None


async def test_paid_calls_stop_when_the_budget_is_spent(t: Tools) -> None:
    session = t.new(budget_credits=5)
    await t.tool("enrich", session=session, full_name="Ada Lovelace", company_domain="acme.com")
    refused = await t.tool("enrich", session=session, full_name="Bob Smith", company_domain="acme.com")
    assert "budget of 5 credits is spent" in refused


async def test_research_is_capped_at_its_api_ceiling(t: Tools, fake: FakeAPI) -> None:
    session = t.new(budget_credits=5000)
    await t.tool("research_company", session=session, company_domain="acme.com")
    await t.tool("verify_email", session=session, email="ada@acme.com")
    assert fake.requests[0][1]["budget_credits"] == 1000.0
    assert fake.requests[2][1]["budget_credits"] == 2000.0


async def test_dry_run_reaches_every_call_and_charges_nothing(t: Tools, fake: FakeAPI) -> None:
    session = t.new(dry_run=True)
    plan = await t.tool("find_leads", session=session, audience="Sales leaders", titles=["Head of Sales"])
    assert plan == {"dry_run": True, "would_cost_up_to": "3", "would_exceed": [], "warnings": []}
    await t.tool("enrich", session=session, full_name="Ada Lovelace", company_domain="acme.com")
    await t.tool("verify_email", session=session, email="ada@acme.com")
    await t.tool(
        "check_companies", session=session, question="Do they sell to hospitals?", company_domains=["acme.com"]
    )
    await t.tool("research_company", session=session, company_domain="acme.com")
    await t.tool("create_campaign", session=session, name="Q4", leads=[{"email": "x@acme.com"}], steps=STEPS)
    await t.tool("launch_campaign", session=session, campaign_id=CAMPAIGN_ID)
    assert len(fake.requests) == 7
    assert all(body["dry_run"] is True for _, body, _ in fake.requests)
    assert not any(path.startswith("/v1/operations/") for path in fake.paths())
    assert session.spent == 0
    assert session.approval_url is None
    assert session.verified == set()


async def test_check_companies_returns_answers_with_evidence(t: Tools) -> None:
    result = await t.tool("check_companies", question="Do they let users book demos?", company_domains=["acme.com"])
    assert result["companies"] == [
        {
            "company_domain": "acme.com",
            "answer": "yes",
            "excerpt": "Book a demo inside the app",
            "source_url": "https://acme.com/product",
        }
    ]
    assert result["credits_charged"] == "3.5"


async def test_research_waits_for_the_operation_and_counts_its_hold(t: Tools, fake: FakeAPI) -> None:
    result = await t.tool("research_company", company_domain="acme.com", audience="We sell payroll software")
    assert result["state"] == "succeeded"
    assert result["result"]["facts"][0]["fact"] == "Raised a Series B"
    assert fake.requests[0][1]["audience"] == "We sell payroll software"
    assert t.session.spent == Decimal(9)


async def test_research_that_outlasts_the_wait_reports_its_operation(t: Tools, fake: FakeAPI) -> None:
    session = t.new(research_timeout=0.01)
    still_running = dict(fake.answers[f"/v1/operations/{OPERATION_ID}"]({}), state="running", result=None)
    fake.answers[f"/v1/operations/{OPERATION_ID}"] = lambda body: still_running
    result = await t.tool("research_company", session=session, company_domain="acme.com")
    assert (result["operation_id"], result["state"]) == (OPERATION_ID, "running")
    assert "Do not start it again" in result["note"]


async def test_api_errors_come_back_as_text(t: Tools, fake: FakeAPI) -> None:
    fake.answers["/v1/verify_email"] = lambda body: httpx.Response(
        402,
        json={"error": {"code": "insufficient_credits", "message": "Top up to continue."}},
        headers={"x-request-id": "req_9"},
    )
    output = await t.tool("verify_email", email="ada@acme.com")
    assert output.startswith("Refused: insufficient_credits: Top up to continue.")
    assert t.session.spent == 0


async def test_get_me_is_free_and_shows_the_run(t: Tools, fake: FakeAPI) -> None:
    me = await t.tool("get_me")
    assert me == {
        "workspace": "Test workspace",
        "credits_available": "250",
        "run_budget": "100",
        "run_spent": "0",
        "dry_run": False,
    }
    assert fake.requests[0][2] is None


async def test_raw_arguments_are_validated_into_readable_text(t: Tools, fake: FakeAPI) -> None:
    output = await t.call("check_companies", {"question": "short", "company_domains": []})
    assert output.startswith("Refused: invalid arguments: question:")
    assert "company_domains" in output
    assert (await t.call("send_everything", {})).startswith("Refused: unknown tool 'send_everything'")
    assert fake.requests == []
    found = json.loads(await t.call("find_leads", {"audience": "Sales leaders", "titles": ["Head of Sales"]}))
    assert found["leads"][0]["company_domain"] == "acme.com"


async def _resolve(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


async def test_verb_methods_raise_and_run_returns_text(t: Tools) -> None:
    args = tools.FindLeadsArgs(audience="Anyone at all")
    with pytest.raises(tools.Refused, match="Add at least one"):
        await _resolve(t.session.find_leads(args))
    text = await _resolve(t.session.run("find_leads", args))
    assert text.startswith("Refused: Add at least one")
    verified = await _resolve(t.session.verify_email(tools.VerifyEmailArgs(email="ada@acme.com")))
    assert json.loads(verified)["keep"] is True


async def test_parallel_async_calls_keep_the_budget_exact(fake: FakeAPI) -> None:
    async with AsyncLayrcake(api_key=API_KEY, base_url=BASE_URL, max_retries=0) as client:
        session = tools.AsyncLayrcakeSession(client, budget_credits=5)
        outputs = await asyncio.gather(
            session.call("enrich", {"full_name": "Ada Lovelace", "company_domain": "acme.com"}),
            session.call("enrich", {"full_name": "Bob Smith", "company_domain": "acme.com"}),
        )
    assert sum(output.startswith("Refused: The run budget") for output in outputs) == 1
    assert len(fake.requests) == 1
    assert session.spent == Decimal(5)


@pytest.mark.parametrize("session_class", [tools.LayrcakeSession, tools.AsyncLayrcakeSession])
def test_budget_must_be_positive(session_class: Any) -> None:
    with pytest.raises(ValueError, match="budget_credits"):
        session_class(api_key=API_KEY, budget_credits=0)


def _sdk_fields(typed_dict: Any) -> set[str]:
    return set(typing.get_type_hints(typed_dict))


@pytest.mark.parametrize(
    ("model", "sdk_type"),
    [
        (tools.FindLeadsArgs, sdk_types.FindLeadsParams),
        (tools.Headcount, sdk_types.FindLeadsParamsCompanyHeadcount),
        (tools.EnrichArgs, sdk_types.EnrichParamsPerson),
        (tools.VerifyEmailArgs, sdk_types.VerifyEmailParams),
        (tools.CheckCompaniesArgs, sdk_types.CheckCompaniesParams),
        (tools.ResearchCompanyArgs, sdk_types.ResearchCompanyParams),
        (tools.CreateCampaignArgs, sdk_types.CreateCampaignParams),
        (tools.Lead, sdk_types.CreateCampaignParamsLeadsItem),
        (tools.Step, sdk_types.CreateCampaignParamsStepsItem),
        (tools.LaunchCampaignArgs, sdk_types.LaunchCampaignParams),
    ],
)
def test_argument_models_follow_the_sdk_contract(model: Any, sdk_type: Any) -> None:
    assert set(model.model_fields) <= _sdk_fields(sdk_type)


def test_every_tool_says_what_it_costs() -> None:
    assert [spec.name for spec in tools.TOOL_SPECS] == list(tools.SPECS)
    for spec in tools.TOOL_SPECS:
        assert "credit" in spec.description.lower() or "free" in spec.description.lower(), spec.name
        assert "—" not in spec.description
        assert spec.args.model_json_schema()["type"] == "object"
    assert "approve" in tools.SPECS["launch_campaign"].description


def test_every_tool_has_a_verb_on_both_sessions() -> None:
    for spec in tools.TOOL_SPECS:
        assert callable(getattr(tools.LayrcakeSession, spec.name))
        assert inspect.iscoroutinefunction(getattr(tools.AsyncLayrcakeSession, spec.name))


def test_the_core_package_does_not_need_pydantic() -> None:
    import layrcake

    assert "agent_tools" not in layrcake.__all__
