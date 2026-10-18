"""Helpers for approvals and background operations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, FrozenSet, Mapping, Optional, Union

from ._response import LayrcakeResponse

__all__ = [
    "TERMINAL_OPERATION_STATES",
    "ApprovalRequest",
    "approval_required",
]

TERMINAL_OPERATION_STATES: FrozenSet[str] = frozenset(
    {"succeeded", "partially_succeeded", "failed", "cancelled", "unknown"}
)
"""States after which wait_for_operation stops polling.

`unknown` means a provider's outcome could not be confirmed and Layrcake is
reconciling it. Do not repeat the action while an operation is `unknown`.
"""

# Result statuses that mean the action already ran, even if a token is echoed back.
_DONE_STATUSES = frozenset({"launched", "purchased", "sent", "approved", "in_progress"})


@dataclass(frozen=True)
class ApprovalRequest:
    """What a person needs to approve before an external action runs.

    Show `approval_url` to a person. After they approve, repeat the same call
    with `confirmation_token`. Where Layrcake runs approved actions itself, it
    starts the action without a second call. The SDK never approves anything.
    """

    confirmation_token: str
    """Single-use token. Send it back on the same call once a person approves."""

    approval_url: Optional[str]
    """Console page where a signed-in person approves. None if the API sent none."""

    approval_email: Optional[str]
    """Whether an approval email went out: queued, reused, suppressed_rate_limit or no_approver."""


def approval_required(response: Union[LayrcakeResponse[Any], Mapping[str, Any]]) -> Optional[ApprovalRequest]:
    """Return what a person must approve, or None when the call needs no approval.

    Pass the response of launch_campaign, setup_sending, reply_to_thread or
    add_leads, or its `result`. A finished action returns None.

    Example:
        preview = client.launch_campaign(campaign_id=campaign_id)
        approval = approval_required(preview)
        if approval is not None:
            print("Approve here:", approval.approval_url)
    """
    result: Any = response.result if isinstance(response, LayrcakeResponse) else response
    if isinstance(result, Mapping) and "result" in result and "confirmation_token" not in result:
        # A raw response body was passed. Read its result.
        result = result["result"]
    if not isinstance(result, Mapping):
        return None
    token = result.get("confirmation_token")
    if not isinstance(token, str) or not token:
        return None
    status = result.get("status")
    if isinstance(status, str) and status in _DONE_STATUSES:
        return None
    url = result.get("approval_url")
    email = result.get("approval_email")
    return ApprovalRequest(
        confirmation_token=token,
        approval_url=url if isinstance(url, str) and url else None,
        approval_email=email if isinstance(email, str) and email else None,
    )
