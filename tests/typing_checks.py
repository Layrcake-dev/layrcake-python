"""Static checks for the public types. mypy reads this file. pytest does not run it."""

from __future__ import annotations

from decimal import Decimal
from typing import List, Optional

from layrcake import ApprovalRequest, AsyncLayrcake, Layrcake, LayrcakeResponse, approval_required
from layrcake.types import (
    FindLeadsResult,
    FindLeadsResultData,
    FindLeadsResultDryRun,
    GetMeResult,
    GetOperationResult,
)


def sync_types(client: Layrcake) -> None:
    live: LayrcakeResponse[FindLeadsResultData] = client.find_leads(audience="CTOs", limit=5)
    names: List[Optional[str]] = [lead["full_name"] for lead in live.result["leads"]]
    plan: LayrcakeResponse[FindLeadsResultDryRun] = client.find_leads(audience="CTOs", dry_run=True)
    ceiling: str = plan.result["plan"]["would"]["maximum_credits"]
    flag = bool(names)
    either: LayrcakeResponse[FindLeadsResult] = client.find_leads(audience="CTOs", dry_run=flag)
    me: LayrcakeResponse[GetMeResult] = client.get_me()
    workspace: str = me.result["workspace"]["name"]
    charged: Optional[Decimal] = live.credits_charged
    operation: GetOperationResult = client.wait_for_operation("op")
    approval: Optional[ApprovalRequest] = approval_required(client.launch_campaign(campaign_id="c"))
    if approval is not None:
        token: str = approval.confirmation_token
        client.launch_campaign(campaign_id="c", confirmation_token=token)
    client.find_leads(audience="CTOs", limit="five")  # type: ignore[call-overload]
    client.verify_email()  # type: ignore[call-overload]
    del ceiling, either, workspace, charged, operation


async def async_types(client: AsyncLayrcake) -> None:
    live: LayrcakeResponse[FindLeadsResultData] = await client.find_leads(audience="CTOs")
    plan: LayrcakeResponse[FindLeadsResultDryRun] = await client.find_leads(audience="CTOs", dry_run=True)
    del live, plan
