"""Layrcake tools for AI agents, for any framework.

One place holds the rules every Layrcake agent integration follows, so the
LangChain, LlamaIndex, CrewAI, OpenAI Agents and LangGraph adapters only map
these tools onto their framework. Every API call goes through this SDK's
client; there is no second HTTP client.

The rules (from https://layrcake.dev/agents.md):

- Every paid call carries the run budget that is left as `budget_credits`, and
  no paid call starts once the budget is spent.
- `dry_run` reaches every call. A dry run returns the most a call could cost
  and charges nothing.
- Every call sends an idempotency key derived from the run and its arguments,
  so a repeated call returns the first answer instead of charging twice.
- Only addresses verified in this run go into a campaign (unless turned off).
- `launch_campaign` never takes a confirmation token. It returns a preview and
  an approval link, and a person approves in the Layrcake console. Nothing in
  this module can approve a send.
- Errors come back as readable text starting with "Refused:", so the model can
  fix the call or stop.

Install with the `agents` extra, which adds pydantic for the argument models:

    pip install "layrcake[agents]"

Use one session per agent run:

    from layrcake.agent_tools import TOOL_SPECS, LayrcakeSession

    session = LayrcakeSession(budget_credits=50)  # reads LAYRCAKE_API_KEY
    for spec in TOOL_SPECS:
        print(spec.name, spec.description, spec.args.model_json_schema())
    print(session.call("verify_email", {"email": "ada@example.com"}))

`AsyncLayrcakeSession` is the same with an `AsyncLayrcake` client: `call`,
`run` and the verb methods are coroutines.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import threading
import uuid
from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    Dict,
    FrozenSet,
    Generator,
    List,
    Mapping,
    Optional,
    Set,
    Tuple,
    Type,
    Union,
    cast,
)

try:
    from pydantic import BaseModel, Field, ValidationError
except ImportError as exc:  # pragma: no cover - exercised only without the extra
    raise ImportError("layrcake.agent_tools needs pydantic 2. Install it with: pip install 'layrcake[agents]'") from exc

from ._client import AsyncLayrcake, Layrcake
from ._exceptions import LayrcakeError, OperationTimeoutError
from ._helpers import approval_required
from ._response import LayrcakeResponse

if TYPE_CHECKING:
    from .types import (
        CheckCompaniesResultData,
        CreateCampaignParamsLeadsItem,
        CreateCampaignParamsStepsItem,
        CreateCampaignResultData,
        EnrichParamsPerson,
        FindLeadsParamsCompanyHeadcount,
        FindLeadsResultData,
        GetMeResult,
        GetOperationResult,
        LaunchCampaignResultData,
        ResearchCompanyResultData,
        VerifyEmailResultData,
    )

__all__ = [
    "DEFAULT_BUDGET_CREDITS",
    "SENDABLE",
    "SPECS",
    "TOOL_SPECS",
    "AsyncLayrcakeSession",
    "CheckCompaniesArgs",
    "CreateCampaignArgs",
    "EnrichArgs",
    "FindLeadsArgs",
    "Headcount",
    "LaunchCampaignArgs",
    "LayrcakeSession",
    "Lead",
    "NoArgs",
    "Refused",
    "ResearchCompanyArgs",
    "Step",
    "ToolSpec",
    "VerifyEmailArgs",
    "invalid_arguments",
]

SENDABLE: FrozenSet[str] = frozenset({"valid", "catch_all_valid"})
"""verify_email answers that are safe to send to."""

DEFAULT_BUDGET_CREDITS = Decimal(50)
"""The run budget when none is given, in credits."""

_MAX_CALL_BUDGET = {"research_company": Decimal(1000)}  # the API's ceiling per call; 2000 elsewhere
_DEFAULT_MAX_CALL_BUDGET = Decimal(2000)
_CENT = Decimal("0.01")


class Refused(Exception):
    """A call these tools will not make. The message is shown to the model."""


# Argument models. Field names and limits follow the SDK's request types
# (layrcake.types.*Params); tests check every field against them.


class Headcount(BaseModel):
    """A company size range."""

    min: Optional[int] = Field(default=None, ge=1, description="Fewest employees.")
    max: Optional[int] = Field(default=None, ge=1, description="Most employees.")


class FindLeadsArgs(BaseModel):
    """Arguments for find_leads."""

    audience: str = Field(min_length=3, description="Who you want, in plain words.")
    titles: Optional[List[str]] = Field(
        default=None, max_length=50, description='Job titles, for example ["Head of Sales", "VP Sales"].'
    )
    industries: Optional[List[str]] = Field(
        default=None, max_length=50, description='LinkedIn industry names, for example ["Software Development"].'
    )
    locations: Optional[List[str]] = Field(
        default=None, max_length=50, description='Places, for example ["London, United Kingdom"].'
    )
    company_domains: Optional[List[str]] = Field(
        default=None, max_length=200, description="Only people at these company domains."
    )
    company_headcount: Optional[Headcount] = Field(default=None, description="Company size range.")
    limit: int = Field(default=10, ge=1, le=25, description="How many people, 1 to 25.")


class EnrichArgs(BaseModel):
    """Arguments for enrich."""

    full_name: str = Field(min_length=1, description="The person's full name.")
    company_domain: str = Field(min_length=1, description="Their company's website domain, for example acme.com.")


class VerifyEmailArgs(BaseModel):
    """Arguments for verify_email."""

    email: str = Field(min_length=3, max_length=320, description="The address to check.")


class CheckCompaniesArgs(BaseModel):
    """Arguments for check_companies."""

    question: str = Field(
        min_length=10,
        max_length=300,
        description="One yes or no question about each company's own offering, for example "
        '"Do they let their users book appointments inside their product?"',
    )
    company_domains: List[str] = Field(
        min_length=1, max_length=20, description='Company website domains, up to 20, for example ["acme.com"].'
    )


class ResearchCompanyArgs(BaseModel):
    """Arguments for research_company."""

    company_domain: str = Field(min_length=1, max_length=2048, description="Company website domain, e.g. acme.com.")
    audience: Optional[str] = Field(
        default=None, description="Optional goal, for example what you sell and to whom, to rank the facts."
    )


class Lead(BaseModel):
    """One campaign recipient."""

    email: str = Field(description="A work email verified in this run.")
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    company_name: Optional[str] = None
    location: Optional[str] = Field(default=None, max_length=200)


class Step(BaseModel):
    """One email in the sequence."""

    subject: Optional[str] = Field(default=None, max_length=300, description="Subject line.")
    body: str = Field(min_length=1, description="Plain text. {{first_name}} is replaced per lead.")
    wait_days: int = Field(default=0, ge=0, le=60, description="Days after the previous step. Ignored on step one.")


class CreateCampaignArgs(BaseModel):
    """Arguments for create_campaign."""

    name: str = Field(min_length=1, max_length=120, description="Campaign name.")
    leads: List[Lead] = Field(min_length=1, max_length=5000, description="Recipients with verified emails.")
    steps: List[Step] = Field(min_length=1, max_length=3, description="One to three emails.")


class LaunchCampaignArgs(BaseModel):
    """Arguments for launch_campaign. There is deliberately no confirmation_token."""

    campaign_id: str = Field(min_length=1, description="The draft's campaign_id from create_campaign.")


class NoArgs(BaseModel):
    """get_me takes no arguments."""


@dataclass(frozen=True)
class ToolSpec:
    """One tool: its name, what the model is told, and its argument model.

    Adapters give a framework `name`, `description` and `args` (or
    `args.model_json_schema()`), then pass the model's arguments to
    `session.call(name, arguments)`.
    """

    name: str
    description: str
    args: Type[BaseModel]


TOOL_SPECS: Tuple[ToolSpec, ...] = (
    ToolSpec(
        "find_leads",
        "Find people who match an audience. Returns names, titles and companies, without emails. "
        "Needs at least one of titles, industries, locations or company_domains. "
        "Costs 0.25 credits per person found, at least 3 credits when anyone is found; finding nobody is free.",
        FindLeadsArgs,
    ),
    ToolSpec(
        "enrich",
        "Find one person's work email from their full name and company domain. "
        "Costs 5 credits when an email is found; free when not.",
        EnrichArgs,
    ),
    ToolSpec(
        "verify_email",
        "Check that an email address can receive mail. Keep valid and catch_all_valid; drop everything else. "
        "Costs 1.25 credits per check.",
        VerifyEmailArgs,
    ),
    ToolSpec(
        "check_companies",
        "Answer one yes or no question about each company in a list, from each company's own website, "
        "with the quote that shows it. Costs 3.5 credits per company answered; unclear answers and "
        "unreadable sites are free.",
        CheckCompaniesArgs,
    ),
    ToolSpec(
        "research_company",
        "Research one company from public sources and return dated facts with their source links. "
        "Costs 9 credits per completed research; finding nothing is free. Can take a few minutes.",
        ResearchCompanyArgs,
    ),
    ToolSpec(
        "create_campaign",
        "Save a draft email campaign for people whose emails were verified in this run, with one to three "
        "emails. A draft never sends. Free.",
        CreateCampaignArgs,
    ),
    ToolSpec(
        "launch_campaign",
        "Ask a person to approve a draft campaign. This sends nothing: it returns a preview and an approval "
        "link, and no email goes out until a person approves it in the Layrcake console. Free. "
        "Call it once, last, then stop and report the approval link.",
        LaunchCampaignArgs,
    ),
    ToolSpec(
        "get_me",
        "Show the workspace, the credits available and this run's budget. Free.",
        NoArgs,
    ),
)
"""Every tool, in the order agents are given them."""

SPECS: Dict[str, ToolSpec] = {spec.name: spec for spec in TOOL_SPECS}
"""TOOL_SPECS by name."""


def invalid_arguments(error: ValidationError) -> str:
    """Describe bad tool arguments so the model can fix the call."""
    problems = "; ".join(f"{'.'.join(str(p) for p in e['loc']) or 'input'}: {e['msg']}" for e in error.errors())
    return f"Refused: invalid arguments: {problems}"


def _json(**values: Any) -> str:
    return json.dumps(values, default=str)


def _plan(result: Any) -> Optional[str]:
    """Return the dry-run plan as text: the most the call could cost. Nothing is charged."""
    if isinstance(result, dict) and result.get("dry_run") is True and "plan" in result:
        plan = result["plan"]
        return _json(
            dry_run=True,
            would_cost_up_to=plan.get("credits_estimate"),
            would_exceed=plan.get("would_exceed", []),
            warnings=plan.get("warnings", []),
        )
    return None


def _without_tokens(value: Any) -> Any:
    """Drop confirmation tokens from what the model reads. Only a person approves a send."""
    if isinstance(value, dict):
        return {k: _without_tokens(v) for k, v in value.items() if k != "confirmation_token"}
    if isinstance(value, list):
        return [_without_tokens(v) for v in value]
    return value


def _describe(error: Exception) -> str:
    if isinstance(error, LayrcakeError):
        text = f"Refused: {error.code}: {error.message}" if error.code else f"Refused: {error.message}"
        if error.request_id:
            text += f" (request {error.request_id})"
        return text
    return f"Refused: {error}"


# Each verb is written once, as a generator that yields the SDK calls it needs
# and receives their responses. LayrcakeSession runs the calls on a Layrcake
# client, AsyncLayrcakeSession awaits them on an AsyncLayrcake client, so the
# sync and async tools cannot drift apart.


@dataclass(frozen=True)
class _Call:
    method: str
    """The client method to call."""

    kwargs: Dict[str, Any]
    """Its arguments. For a paid call, the budget, dry_run and idempotency key are added when it runs."""

    verb: Optional[str] = None
    """Set on paid calls: the budget rules apply and the call's cost is counted."""


_Steps = Generator[_Call, Any, str]


class _Run:
    """The run state and the verbs. Shared by LayrcakeSession and AsyncLayrcakeSession."""

    def __init__(
        self,
        *,
        budget_credits: Union[float, int, str, Decimal],
        dry_run: bool,
        run_id: Optional[str],
        require_verified: bool,
        research_timeout: float,
    ) -> None:
        self.budget = Decimal(str(budget_credits))
        if self.budget <= 0:
            raise ValueError("budget_credits must be above 0")
        self.dry_run = dry_run
        self.run_id = run_id or uuid.uuid4().hex[:12]
        self.require_verified = require_verified
        self.research_timeout = research_timeout
        self.spent = Decimal(0)
        self.found = 0
        self.verified: Set[str] = set()
        self.catch_all: Set[str] = set()
        self.approval_url: Optional[str] = None
        self.launch_error: Optional[str] = None

    def left(self) -> Decimal:
        """Credits left in this run's budget."""
        return self.budget - self.spent

    def key(self, verb: str, args: Any) -> str:
        """Return the idempotency key: same call, same key, so a repeat never charges twice."""
        digest = hashlib.sha256(json.dumps([args, self.dry_run], sort_keys=True, default=str).encode()).hexdigest()
        return f"{self.run_id}-{verb}-{digest[:24]}"

    def _validate(self, name: str, arguments: Optional[Mapping[str, Any]]) -> Union[BaseModel, str]:
        spec = SPECS.get(name)
        if spec is None:
            return f"Refused: unknown tool {name!r}. Use one of: {', '.join(SPECS)}."
        try:
            return spec.args.model_validate(dict(arguments or {}))
        except ValidationError as error:
            return invalid_arguments(error)

    def _steps(self, name: str, args: BaseModel) -> _Steps:
        verbs: Dict[str, Callable[[Any], _Steps]] = {
            "find_leads": self._find_leads,
            "enrich": self._enrich,
            "verify_email": self._verify_email,
            "check_companies": self._check_companies,
            "research_company": self._research_company,
            "create_campaign": self._create_campaign,
            "launch_campaign": self._launch_campaign,
            "get_me": self._get_me,
        }
        if name not in verbs:
            raise Refused(f"unknown tool {name!r}. Use one of: {', '.join(SPECS)}.")
        return verbs[name](args)

    def _admit(self, verb: str, call: _Call) -> Dict[str, Any]:
        """Refuse past the budget or after the approval link, and cap the call at what is left. Call under the lock."""
        if self.approval_url:
            raise Refused("The campaign is waiting for approval. Stop and report the approval link.")
        if self.left() < _CENT:
            raise Refused(f"The run budget of {self.budget} credits is spent. Stop and report.")
        cap = min(self.left(), _MAX_CALL_BUDGET.get(verb, _DEFAULT_MAX_CALL_BUDGET))
        return {
            **call.kwargs,
            "budget_credits": float(cap.quantize(_CENT, rounding=ROUND_DOWN)),
            "dry_run": self.dry_run,
            "idempotency_key": self.key(verb, call.kwargs),
        }

    def _count(self, verb: str, response: LayrcakeResponse[Any]) -> None:
        """Add what a paid call cost to the run. Call under the lock."""
        charged = response.credits_charged or Decimal(0)
        if verb == "research_company" and isinstance(response.result, dict):
            # Research is charged when it finishes. Count its hold now so the budget stays a ceiling.
            charged = max(charged, Decimal(str(response.result.get("credits_reserved") or 0)))
        self.spent += charged

    def _spend(self, response: LayrcakeResponse[Any]) -> Dict[str, Any]:
        return {"credits_charged": response.credits_charged, "budget_left": self.left()}

    def _find_leads(self, args: FindLeadsArgs) -> _Steps:
        if not (args.titles or args.industries or args.locations or args.company_domains):
            raise Refused("Add at least one of titles, industries, locations or company_domains.")
        payload: Dict[str, Any] = {"audience": args.audience, "limit": args.limit}
        for name in ("titles", "industries", "locations", "company_domains"):
            value = getattr(args, name)
            if value:
                payload[name] = value
        if args.company_headcount:
            headcount = cast("FindLeadsParamsCompanyHeadcount", args.company_headcount.model_dump(exclude_none=True))
            payload["company_headcount"] = headcount
        response: LayrcakeResponse[Any] = yield _Call("find_leads", payload, verb="find_leads")
        plan = _plan(response.result)
        if plan:
            return plan
        data = cast("FindLeadsResultData", response.result)
        keep = ("full_name", "title", "company_name", "company_domain", "location", "linkedin_url")
        leads = [{k: lead.get(k) for k in keep} for lead in data["leads"]]
        self.found += len(leads)
        return _json(leads=leads, **self._spend(response))

    def _enrich(self, args: EnrichArgs) -> _Steps:
        person: EnrichParamsPerson = {"full_name": args.full_name, "company_domain": args.company_domain}
        response: LayrcakeResponse[Any] = yield _Call(
            "enrich", {"person": person, "want": ["work_email"]}, verb="enrich"
        )
        plan = _plan(response.result)
        if plan:
            return plan
        contact = response.result.get("contact") or {}
        return _json(work_email=contact.get("work_email"), **self._spend(response))

    def _verify_email(self, args: VerifyEmailArgs) -> _Steps:
        response: LayrcakeResponse[Any] = yield _Call("verify_email", {"email": args.email}, verb="verify_email")
        plan = _plan(response.result)
        if plan:
            return plan
        data = cast("VerifyEmailResultData", response.result)
        keep = data["status"] in SENDABLE
        if keep:
            address = args.email.strip().lower()
            self.verified.add(address)
            if data["status"] == "catch_all_valid":
                self.catch_all.add(address)
        return _json(email=args.email, status=data["status"], keep=keep, **self._spend(response))

    def _check_companies(self, args: CheckCompaniesArgs) -> _Steps:
        payload = {"question": args.question, "company_domains": args.company_domains}
        response: LayrcakeResponse[Any] = yield _Call("check_companies", payload, verb="check_companies")
        plan = _plan(response.result)
        if plan:
            return plan
        data = cast("CheckCompaniesResultData", response.result)
        keep = ("company_domain", "answer", "excerpt", "source_url")
        companies = [{k: row.get(k) for k in keep} for row in data["companies"]]
        return _json(question=data["question"], companies=companies, **self._spend(response))

    def _research_company(self, args: ResearchCompanyArgs) -> _Steps:
        payload: Dict[str, Any] = {"company_domain": args.company_domain}
        if args.audience:
            payload["audience"] = args.audience
        response: LayrcakeResponse[Any] = yield _Call("research_company", payload, verb="research_company")
        plan = _plan(response.result)
        if plan:
            return plan
        started = cast("ResearchCompanyResultData", response.result)
        try:
            operation: GetOperationResult = yield _Call(
                "wait_for_operation", {"operation_id": started["operation_id"], "timeout": self.research_timeout}
            )
        except OperationTimeoutError:
            return _json(
                operation_id=started["operation_id"],
                state="running",
                note=f"Still running after {self.research_timeout:g} seconds. Do not start it again; "
                "the result will appear in the Layrcake console.",
                credits_reserved=started["credits_reserved"],
                budget_left=self.left(),
            )
        return _json(
            operation_id=started["operation_id"],
            state=operation["state"],
            result=operation.get("result"),
            error=operation.get("error"),
            credits_reserved=started["credits_reserved"],
            budget_left=self.left(),
        )

    def _create_campaign(self, args: CreateCampaignArgs) -> _Steps:
        if self.require_verified and not self.dry_run:
            unverified = [lead.email for lead in args.leads if lead.email.strip().lower() not in self.verified]
            if unverified:
                raise Refused(f"Verify these first with verify_email, or drop them: {', '.join(unverified)}")
        leads = [cast("CreateCampaignParamsLeadsItem", lead.model_dump(exclude_none=True)) for lead in args.leads]
        steps = [cast("CreateCampaignParamsStepsItem", step.model_dump(exclude_none=True)) for step in args.steps]
        response: LayrcakeResponse[Any] = yield _Call(
            "create_campaign",
            {
                "name": args.name,
                "leads": leads,
                "steps": steps,
                "dry_run": self.dry_run,
                "idempotency_key": self.key("create_campaign", [args.name, leads, steps]),
            },
        )
        plan = _plan(response.result)
        if plan:
            return plan
        draft = cast("CreateCampaignResultData", response.result)
        return _json(
            campaign_id=draft["campaign_id"],
            state=draft["state"],
            leads_accepted=draft["leads_accepted"],
            leads_suppressed=draft["leads_suppressed"],
        )

    def _launch_campaign(self, args: LaunchCampaignArgs) -> _Steps:
        try:
            response: LayrcakeResponse[Any] = yield _Call(
                "launch_campaign",
                {
                    "campaign_id": args.campaign_id,
                    "dry_run": self.dry_run,
                    "idempotency_key": self.key("launch_campaign", args.campaign_id),
                },
            )
        except LayrcakeError as error:
            self.launch_error = f"{error.code}: {error.message}" if error.code else error.message
            raise
        plan = _plan(response.result)
        if plan:
            return plan
        data = cast("LaunchCampaignResultData", response.result)
        approval = approval_required(response)
        if approval is not None:
            self.approval_url = approval.approval_url or "(approval requested in the Layrcake console)"
        return _json(
            status=data["status"],
            campaign_id=data["campaign_id"],
            approval_url=approval.approval_url if approval else None,
            next_step="A person must approve this in the Layrcake console before anything sends. Stop here."
            if approval
            else None,
            preview=_without_tokens({k: v for k, v in data.items() if k not in ("status", "campaign_id")}),
        )

    def _get_me(self, args: Optional[BaseModel] = None) -> _Steps:
        response: LayrcakeResponse[GetMeResult] = yield _Call("get_me", {})
        me = response.result
        balance = me.get("credits")
        return _json(
            workspace=me["workspace"].get("name"),
            credits_available=balance["available"] if balance else None,
            run_budget=self.budget,
            run_spent=self.spent,
            dry_run=self.dry_run,
        )


class LayrcakeSession(_Run):
    """The state one agent run shares across its Layrcake tools, on a sync `Layrcake` client.

    Create one per run. It holds the run budget, the dry_run flag, the emails
    verified so far and, once launch_campaign has asked for approval, the
    approval link, after which no further paid call runs. Paid calls run one at
    a time, so the budget stays exact when a framework calls tools in threads.

    Adapters call `call(name, arguments)` with the model's raw arguments. It
    validates them, runs the tool and returns the text the model reads. It never
    raises for bad arguments, API errors or rule refusals.

    Args:
        client: A `layrcake.Layrcake` client. Built from `api_key` or the
            LAYRCAKE_API_KEY environment variable when omitted.
        api_key: Used only when `client` is omitted.
        budget_credits: The most this run may spend, in credits. Default 50.
        dry_run: Return every call's plan instead of running it. Charges nothing.
        run_id: Prefix for idempotency keys. Reuse it to resume a run without paying twice.
        require_verified: Refuse campaign leads not verified in this run. Default True.
        research_timeout: Seconds research_company waits for its result. Default 300.

    Attributes:
        spent: Credits this run has spent (research counts its hold).
        found: People find_leads has returned in this run.
        verified: Lower-cased addresses verify_email kept in this run.
        catch_all: The subset of `verified` that came back catch_all_valid.
        approval_url: Set once launch_campaign has asked a person to approve.
        launch_error: Why launch_campaign was refused, when it was.
    """

    def __init__(
        self,
        client: Optional[Layrcake] = None,
        *,
        api_key: Optional[str] = None,
        budget_credits: Union[float, int, str, Decimal] = DEFAULT_BUDGET_CREDITS,
        dry_run: bool = False,
        run_id: Optional[str] = None,
        require_verified: bool = True,
        research_timeout: float = 300.0,
    ) -> None:
        super().__init__(
            budget_credits=budget_credits,
            dry_run=dry_run,
            run_id=run_id,
            require_verified=require_verified,
            research_timeout=research_timeout,
        )
        self.client = client if client is not None else Layrcake(api_key=api_key)
        self._lock = threading.Lock()  # one paid call at a time keeps the budget exact

    def _send(self, call: _Call) -> Any:
        method = getattr(self.client, call.method)
        if call.verb is None:
            return method(**call.kwargs)
        with self._lock:
            response = method(**self._admit(call.verb, call))
            self._count(call.verb, response)
            return response

    def _drive(self, steps: _Steps) -> str:
        try:
            call = next(steps)
            while True:
                try:
                    response = self._send(call)
                except Exception as error:
                    call = steps.throw(error)
                else:
                    call = steps.send(response)
        except StopIteration as done:
            return cast(str, done.value)

    def call(self, name: str, arguments: Optional[Mapping[str, Any]] = None) -> str:
        """Validate raw tool arguments, run the tool and return what the model reads. Never raises."""
        args = self._validate(name, arguments)
        if isinstance(args, str):
            return args
        return self.run(name, args)

    def run(self, name: str, args: BaseModel) -> str:
        """Run one tool on validated arguments and return what the model reads. Never raises for API or rule errors."""
        try:
            return self._drive(self._steps(name, args))
        except (LayrcakeError, Refused) as error:
            return _describe(error)

    # One method per verb. Each raises Refused or LayrcakeError instead of returning "Refused:" text.

    def find_leads(self, args: FindLeadsArgs) -> str:
        """Run find_leads."""
        return self._drive(self._find_leads(args))

    def enrich(self, args: EnrichArgs) -> str:
        """Run enrich for a work email."""
        return self._drive(self._enrich(args))

    def verify_email(self, args: VerifyEmailArgs) -> str:
        """Run verify_email and remember sendable addresses."""
        return self._drive(self._verify_email(args))

    def check_companies(self, args: CheckCompaniesArgs) -> str:
        """Run check_companies."""
        return self._drive(self._check_companies(args))

    def research_company(self, args: ResearchCompanyArgs) -> str:
        """Start research_company and wait for its result."""
        return self._drive(self._research_company(args))

    def create_campaign(self, args: CreateCampaignArgs) -> str:
        """Save a draft campaign. Drafts never send."""
        return self._drive(self._create_campaign(args))

    def launch_campaign(self, args: LaunchCampaignArgs) -> str:
        """Ask a person to approve the draft. Sends nothing; there is no way to pass a confirmation token."""
        return self._drive(self._launch_campaign(args))

    def get_me(self, args: Optional[BaseModel] = None) -> str:
        """Show the workspace, its credits and this run's budget. Free."""
        return self._drive(self._get_me(args))


class AsyncLayrcakeSession(_Run):
    """`LayrcakeSession` for asyncio, on an `AsyncLayrcake` client. `call`, `run` and the verbs are coroutines.

    The rules, arguments and attributes are the same as LayrcakeSession's. Paid
    calls hold an asyncio lock, so parallel tool calls still run one at a time.
    """

    def __init__(
        self,
        client: Optional[AsyncLayrcake] = None,
        *,
        api_key: Optional[str] = None,
        budget_credits: Union[float, int, str, Decimal] = DEFAULT_BUDGET_CREDITS,
        dry_run: bool = False,
        run_id: Optional[str] = None,
        require_verified: bool = True,
        research_timeout: float = 300.0,
    ) -> None:
        super().__init__(
            budget_credits=budget_credits,
            dry_run=dry_run,
            run_id=run_id,
            require_verified=require_verified,
            research_timeout=research_timeout,
        )
        self.client = client if client is not None else AsyncLayrcake(api_key=api_key)
        self._lock: Optional[asyncio.Lock] = None  # made on first use, inside the running loop

    async def _send(self, call: _Call) -> Any:
        method = getattr(self.client, call.method)
        if call.verb is None:
            return await method(**call.kwargs)
        if self._lock is None:
            self._lock = asyncio.Lock()
        async with self._lock:
            response = await method(**self._admit(call.verb, call))
            self._count(call.verb, response)
            return response

    async def _drive(self, steps: _Steps) -> str:
        try:
            call = next(steps)
            while True:
                try:
                    response = await self._send(call)
                except Exception as error:
                    call = steps.throw(error)
                else:
                    call = steps.send(response)
        except StopIteration as done:
            return cast(str, done.value)

    async def call(self, name: str, arguments: Optional[Mapping[str, Any]] = None) -> str:
        """Validate raw tool arguments, run the tool and return what the model reads. Never raises."""
        args = self._validate(name, arguments)
        if isinstance(args, str):
            return args
        return await self.run(name, args)

    async def run(self, name: str, args: BaseModel) -> str:
        """Run one tool on validated arguments and return what the model reads. Never raises for API or rule errors."""
        try:
            return await self._drive(self._steps(name, args))
        except (LayrcakeError, Refused) as error:
            return _describe(error)

    async def find_leads(self, args: FindLeadsArgs) -> str:
        """Run find_leads."""
        return await self._drive(self._find_leads(args))

    async def enrich(self, args: EnrichArgs) -> str:
        """Run enrich for a work email."""
        return await self._drive(self._enrich(args))

    async def verify_email(self, args: VerifyEmailArgs) -> str:
        """Run verify_email and remember sendable addresses."""
        return await self._drive(self._verify_email(args))

    async def check_companies(self, args: CheckCompaniesArgs) -> str:
        """Run check_companies."""
        return await self._drive(self._check_companies(args))

    async def research_company(self, args: ResearchCompanyArgs) -> str:
        """Start research_company and wait for its result."""
        return await self._drive(self._research_company(args))

    async def create_campaign(self, args: CreateCampaignArgs) -> str:
        """Save a draft campaign. Drafts never send."""
        return await self._drive(self._create_campaign(args))

    async def launch_campaign(self, args: LaunchCampaignArgs) -> str:
        """Ask a person to approve the draft. Sends nothing; there is no way to pass a confirmation token."""
        return await self._drive(self._launch_campaign(args))

    async def get_me(self, args: Optional[BaseModel] = None) -> str:
        """Show the workspace, its credits and this run's budget. Free."""
        return await self._drive(self._get_me(args))
