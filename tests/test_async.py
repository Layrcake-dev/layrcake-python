"""The async client behaves exactly like the sync one."""

from __future__ import annotations

import json
import uuid
from decimal import Decimal
from typing import List

import httpx
import pytest
import respx

from layrcake import (
    APITimeoutError,
    AsyncLayrcake,
    BadRequestError,
    InsufficientCreditsError,
    LayrcakeError,
    OperationTimeoutError,
    approval_required,
)

from .conftest import API_KEY, BASE_URL, envelope, error_body

OK = envelope({"email": "a@b.co"}, charged="0.500000")
OP_ID = "6f1c1d2e-3f40-4a8e-9a0e-0b5c3a522f6c"


async def test_auth_header_and_result(async_client: AsyncLayrcake, api: respx.MockRouter) -> None:
    route = api.post("/v1/verify_email").respond(200, json=OK)
    response = await async_client.verify_email(email="a@b.co")
    request = route.calls.last.request
    assert request.headers["Authorization"] == f"Bearer {API_KEY}"
    assert json.loads(request.content) == {"email": "a@b.co"}
    assert response.result["email"] == "a@b.co"
    assert response.credits_charged == Decimal("0.500000")


async def test_idempotency_key_is_reused_across_retries(
    async_client: AsyncLayrcake, api: respx.MockRouter, sleeps: List[float]
) -> None:
    route = api.post("/v1/enrich").mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "1"}),
            httpx.ConnectError("refused"),
            httpx.Response(200, json=OK),
        ]
    )
    await async_client.enrich(person={"email": "a@b.co"})
    sent = [call.request.headers["Idempotency-Key"] for call in route.calls]
    assert len(sent) == 3
    assert len(set(sent)) == 1
    assert uuid.UUID(sent[0]).version == 4
    assert sleeps[0] == 1.0
    assert len(sleeps) == 2


async def test_no_retry_on_400(async_client: AsyncLayrcake, api: respx.MockRouter, sleeps: List[float]) -> None:
    route = api.post("/v1/verify_email").respond(400, json=error_body("invalid_request"))
    with pytest.raises(BadRequestError):
        await async_client.verify_email(email="nope")
    assert route.call_count == 1
    assert sleeps == []


async def test_error_mapping(async_client: AsyncLayrcake, api: respx.MockRouter) -> None:
    api.post("/v1/find_leads").respond(402, json=error_body("insufficient_credits", request_id="req_1"))
    with pytest.raises(InsufficientCreditsError) as info:
        await async_client.find_leads(audience="CTOs")
    assert info.value.request_id == "req_1"


async def test_timeout(async_client: AsyncLayrcake, api: respx.MockRouter) -> None:
    api.get("/v1/me").mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(APITimeoutError):
        await async_client.get_me()


async def test_wait_for_operation(async_client: AsyncLayrcake, api: respx.MockRouter, sleeps: List[float]) -> None:
    base = {"operation_id": OP_ID, "kind": "prospect_picture", "progress": {"total": 1, "done": 0}, "result": None}
    api.get(f"/v1/operations/{OP_ID}").mock(
        side_effect=[
            httpx.Response(200, json={**base, "state": "running"}),
            httpx.Response(200, json={**base, "state": "failed", "error": "provider down"}),
        ]
    )
    done = await async_client.wait_for_operation(OP_ID, interval=1.5)
    assert done["state"] == "failed"
    assert sleeps == [1.5]


async def test_wait_for_operation_times_out(async_client: AsyncLayrcake, api: respx.MockRouter) -> None:
    api.get(f"/v1/operations/{OP_ID}").respond(200, json={"operation_id": OP_ID, "state": "queued"})
    with pytest.raises(OperationTimeoutError):
        await async_client.wait_for_operation(OP_ID, interval=0.001, timeout=0.005)


async def test_approval_narrowing(async_client: AsyncLayrcake, api: respx.MockRouter) -> None:
    api.post("/v1/setup_sending").respond(
        200,
        json=envelope({"status": "quote", "quote": {}, "confirmation_token": "t", "approval_url": "https://x/a"}),
    )
    approval = approval_required(await async_client.setup_sending(emails_per_week=500))
    assert approval is not None
    assert approval.approval_url == "https://x/a"


async def test_env_fallback(monkeypatch: pytest.MonkeyPatch, api: respx.MockRouter) -> None:
    monkeypatch.setenv("LAYRCAKE_API_KEY", "lc_async_env")
    route = api.get("/v1/me").respond(200, json={})
    async with AsyncLayrcake(base_url=BASE_URL) as client:
        await client.get_me()
    assert route.calls.last.request.headers["Authorization"] == "Bearer lc_async_env"


async def test_missing_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LAYRCAKE_API_KEY", raising=False)
    with pytest.raises(LayrcakeError, match="LAYRCAKE_API_KEY"):
        AsyncLayrcake()


async def test_context_manager_closes_its_own_client() -> None:
    client = AsyncLayrcake(api_key=API_KEY)
    async with client:
        pass
    assert client._transport.client.is_closed


async def test_with_options(async_client: AsyncLayrcake) -> None:
    copy = async_client.with_options(max_retries=0)
    assert copy.max_retries == 0
    assert copy._transport.client is async_client._transport.client
    await copy.close()
    assert not async_client._transport.client.is_closed
