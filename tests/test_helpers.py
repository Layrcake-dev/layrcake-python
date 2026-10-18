from __future__ import annotations

from typing import Any, Dict, List

import httpx
import pytest
import respx

from layrcake import (
    TERMINAL_OPERATION_STATES,
    ApprovalRequest,
    Layrcake,
    OperationTimeoutError,
    approval_required,
)

from .conftest import envelope

CAMPAIGN_ID = "0b5c3a52-2f6c-4a8e-9a0e-6f1c1d2e3f40"
OP_ID = "6f1c1d2e-3f40-4a8e-9a0e-0b5c3a522f6c"


def operation(state: str, **extra: Any) -> Dict[str, Any]:
    return {
        "operation_id": OP_ID,
        "kind": "research_company",
        "state": state,
        "progress": {"total": 3, "done": 1},
        "result": None,
        "error": None,
        "created_at": "2026-10-18T00:00:00Z",
        "completed_at": None,
        **extra,
    }


def test_launch_preview_needs_approval(client: Layrcake, api: respx.MockRouter) -> None:
    api.post("/v1/launch_campaign").respond(
        200,
        json=envelope(
            {
                "status": "needs_confirmation",
                "campaign_id": CAMPAIGN_ID,
                "confirmation_token": "tok_launch",
                "approval_email": "queued",
                "approval": {"id": "a1", "state": "pending_approval", "expires_at": "2026-10-25T00:00:00Z"},
            },
            charged="0",
        ),
    )
    preview = client.launch_campaign(campaign_id=CAMPAIGN_ID)
    approval = approval_required(preview)
    assert approval == ApprovalRequest(confirmation_token="tok_launch", approval_url=None, approval_email="queued")


def test_reply_preview_has_an_approval_url(client: Layrcake, api: respx.MockRouter) -> None:
    api.post("/v1/reply_to_thread").respond(
        200,
        json=envelope(
            {
                "status": "needs_confirmation",
                "confirmation_token": "tok_reply",
                "approval_url": "https://app.layrcake.dev/approvals/a2",
                "preview": {"to_email": "ada@example.com"},
            }
        ),
    )
    approval = approval_required(client.reply_to_thread(reply_id="r1", body_text="Thanks, Ada"))
    assert approval is not None
    assert approval.confirmation_token == "tok_reply"
    assert approval.approval_url == "https://app.layrcake.dev/approvals/a2"
    assert approval.approval_email is None


@pytest.mark.parametrize(
    "result",
    [
        {"status": "quote", "quote": {}, "confirmation_token": "t", "approval_url": "https://x/a"},
        {"campaign_id": "c", "state": "active", "confirmation_token": "t", "approval_url": "https://x/a"},
    ],
)
def test_quotes_and_active_campaign_adds_need_approval(result: Dict[str, Any]) -> None:
    approval = approval_required(result)
    assert approval is not None
    assert approval.approval_url == "https://x/a"


@pytest.mark.parametrize(
    "result",
    [
        {"status": "launched", "campaign_id": "c", "confirmation_token": "echoed"},
        {"status": "sent", "sent_reply_id": "s1"},
        {"status": "purchased", "quote": {}, "confirmation_token": "echoed", "operation_id": "o1"},
        {"status": "approved", "approval": {"state": "approved"}},
        {"status": "in_progress", "request_id": "r"},
        {"dry_run": True, "advisory": True, "evaluated_at": "now", "plan": {}},
        {"campaign_id": "c", "state": "draft", "leads_accepted": 3},
        {"confirmation_token": ""},
        "not a mapping",
    ],
)
def test_no_approval_needed(result: Any) -> None:
    assert approval_required(result) is None


def test_raw_body_is_accepted() -> None:
    body = envelope({"status": "needs_confirmation", "campaign_id": "c", "confirmation_token": "tok"})
    approval = approval_required(body)
    assert approval is not None
    assert approval.confirmation_token == "tok"


def test_wait_for_operation_polls_until_terminal(client: Layrcake, api: respx.MockRouter, sleeps: List[float]) -> None:
    route = api.get(f"/v1/operations/{OP_ID}").mock(
        side_effect=[
            httpx.Response(200, json=operation("queued")),
            httpx.Response(200, json=operation("running")),
            httpx.Response(200, json=operation("succeeded", result={"summary": "ok"})),
        ]
    )
    done = client.wait_for_operation(OP_ID, interval=0.5)
    assert done["state"] == "succeeded"
    assert done["result"] == {"summary": "ok"}
    assert route.call_count == 3
    assert sleeps == [0.5, 0.5]


@pytest.mark.parametrize("state", sorted(TERMINAL_OPERATION_STATES))
def test_wait_for_operation_stops_on_every_terminal_state(client: Layrcake, api: respx.MockRouter, state: str) -> None:
    api.get(f"/v1/operations/{OP_ID}").respond(200, json=operation(state))
    assert client.wait_for_operation(OP_ID)["state"] == state


def test_wait_for_operation_times_out(client: Layrcake, api: respx.MockRouter) -> None:
    api.get(f"/v1/operations/{OP_ID}").respond(200, json=operation("running"))
    client._transport._sleep = lambda seconds: None
    with pytest.raises(OperationTimeoutError) as info:
        client.wait_for_operation(OP_ID, interval=0.001, timeout=0.01)
    assert info.value.code == "operation_timeout"
    assert info.value.operation_id == OP_ID
    assert info.value.operation["state"] == "running"


def test_wait_for_operation_passes_the_cursor_through_get_operation(client: Layrcake, api: respx.MockRouter) -> None:
    route = api.get(f"/v1/operations/{OP_ID}").respond(200, json=operation("succeeded"))
    client.get_operation(OP_ID, cursor="next_1")
    assert route.calls.last.request.url.params["cursor"] == "next_1"


@pytest.mark.parametrize(("interval", "timeout"), [(0, 1), (-1, 1), (1, 0), (1, float("nan"))])
def test_wait_for_operation_checks_its_arguments(client: Layrcake, interval: float, timeout: float) -> None:
    with pytest.raises(ValueError, match="positive"):
        client.wait_for_operation(OP_ID, interval=interval, timeout=timeout)


def test_wait_for_operation_needs_an_id(client: Layrcake) -> None:
    with pytest.raises(ValueError, match="operation_id"):
        client.wait_for_operation("")
