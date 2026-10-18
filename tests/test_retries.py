from __future__ import annotations

import email.utils
import time
import uuid
from typing import List

import httpx
import pytest
import respx

from layrcake import (
    APIConnectionError,
    APITimeoutError,
    BadRequestError,
    ConflictError,
    InternalServerError,
    Layrcake,
    RateLimitError,
)

from .conftest import API_KEY, BASE_URL, envelope, error_body

OK = envelope({"email": "a@b.co"})


def keys(route: respx.Route) -> List[str]:
    return [call.request.headers["Idempotency-Key"] for call in route.calls]


def test_post_gets_a_uuid4_idempotency_key(client: Layrcake, api: respx.MockRouter) -> None:
    route = api.post("/v1/verify_email").respond(200, json=OK)
    client.verify_email(email="a@b.co")
    key = route.calls.last.request.headers["Idempotency-Key"]
    assert uuid.UUID(key).version == 4


def test_each_call_gets_its_own_key(client: Layrcake, api: respx.MockRouter) -> None:
    route = api.post("/v1/verify_email").respond(200, json=OK)
    client.verify_email(email="a@b.co")
    client.verify_email(email="a@b.co")
    first, second = keys(route)
    assert first != second


def test_idempotency_key_is_reused_across_retries(client: Layrcake, api: respx.MockRouter, sleeps: List[float]) -> None:
    route = api.post("/v1/find_leads").mock(
        side_effect=[httpx.Response(503), httpx.Response(502), httpx.Response(200, json=OK)]
    )
    response = client.find_leads(audience="CTOs")
    assert response.status_code == 200
    assert route.call_count == 3
    assert len(set(keys(route))) == 1
    assert len(sleeps) == 2


def test_caller_idempotency_key_is_used_and_reused(client: Layrcake, api: respx.MockRouter) -> None:
    route = api.post("/v1/campaigns").mock(side_effect=[httpx.Response(504), httpx.Response(200, json=OK)])
    client.create_campaign(name="Q4", leads=[{"email": "a@b.co"}], steps=[], idempotency_key="campaign-job-7")
    assert keys(route) == ["campaign-job-7", "campaign-job-7"]


def test_empty_idempotency_key_is_rejected(client: Layrcake) -> None:
    with pytest.raises(ValueError, match="idempotency_key"):
        client.verify_email(email="a@b.co", idempotency_key="")


def test_required_idempotency_key_is_generated(client: Layrcake, api: respx.MockRouter) -> None:
    route = api.post(url__regex=r"/v1/campaigns/.+/pause").respond(200, json={"result": {}})
    client.pause_campaign("c1")
    assert uuid.UUID(route.calls.last.request.headers["Idempotency-Key"])


def test_retry_after_seconds_is_honoured(client: Layrcake, api: respx.MockRouter, sleeps: List[float]) -> None:
    api.post("/v1/verify_email").mock(
        side_effect=[httpx.Response(429, headers={"Retry-After": "2"}), httpx.Response(200, json=OK)]
    )
    client.verify_email(email="a@b.co")
    assert sleeps == [2.0]


def test_retry_after_ms_is_honoured(client: Layrcake, api: respx.MockRouter, sleeps: List[float]) -> None:
    api.post("/v1/verify_email").mock(
        side_effect=[httpx.Response(503, headers={"Retry-After-Ms": "250"}), httpx.Response(200, json=OK)]
    )
    client.verify_email(email="a@b.co")
    assert sleeps == [0.25]


def test_retry_after_http_date_is_honoured(client: Layrcake, api: respx.MockRouter, sleeps: List[float]) -> None:
    when = email.utils.formatdate(time.time() + 30, usegmt=True)
    api.post("/v1/verify_email").mock(
        side_effect=[httpx.Response(429, headers={"Retry-After": when}), httpx.Response(200, json=OK)]
    )
    client.verify_email(email="a@b.co")
    assert len(sleeps) == 1
    assert 27 <= sleeps[0] <= 30


def test_details_retry_after_seconds_is_honoured(client: Layrcake, api: respx.MockRouter, sleeps: List[float]) -> None:
    api.post("/v1/verify_email").mock(
        side_effect=[
            httpx.Response(429, json=error_body("rate_limited", details={"retry_after_seconds": 3})),
            httpx.Response(200, json=OK),
        ]
    )
    client.verify_email(email="a@b.co")
    assert sleeps == [3.0]


def test_long_retry_after_is_raised_not_waited(client: Layrcake, api: respx.MockRouter, sleeps: List[float]) -> None:
    route = api.post("/v1/verify_email").respond(429, headers={"Retry-After": "120"}, json=error_body("rate_limited"))
    with pytest.raises(RateLimitError) as info:
        client.verify_email(email="a@b.co")
    assert info.value.retry_after_seconds == 120.0
    assert route.call_count == 1
    assert sleeps == []


def test_backoff_values(api: respx.MockRouter) -> None:
    recorded: List[float] = []
    with Layrcake(api_key=API_KEY, base_url=BASE_URL, max_retries=5) as c:
        c._transport._sleep = recorded.append
        api.get("/v1/me").respond(503)
        with pytest.raises(InternalServerError):
            c.get_me()
    assert len(recorded) == 5
    for attempt, delay in enumerate(recorded):
        ceiling = min(0.5 * 2**attempt, 8.0)
        assert 0.75 * ceiling <= delay <= ceiling


@pytest.mark.parametrize(
    ("status", "code", "error"),
    [
        (400, "invalid_request", BadRequestError),
        (409, "idempotency_conflict", ConflictError),
        (500, "internal", InternalServerError),
    ],
)
def test_other_errors_are_never_retried(
    client: Layrcake,
    api: respx.MockRouter,
    sleeps: List[float],
    status: int,
    code: str,
    error: type,
) -> None:
    route = api.post("/v1/verify_email").respond(status, json=error_body(code))
    with pytest.raises(error):
        client.verify_email(email="a@b.co")
    assert route.call_count == 1
    assert sleeps == []


def test_retries_run_out_and_raise_the_last_error(client: Layrcake, api: respx.MockRouter, sleeps: List[float]) -> None:
    route = api.post("/v1/verify_email").respond(503, json=error_body("provider_unavailable", "busy"))
    with pytest.raises(InternalServerError) as info:
        client.verify_email(email="a@b.co")
    assert route.call_count == 3
    assert len(sleeps) == 2
    assert info.value.code == "provider_unavailable"
    assert info.value.idempotency_key == keys(route)[0]


def test_max_retries_zero_sends_once(api: respx.MockRouter) -> None:
    route = api.get("/v1/me").respond(502)
    with Layrcake(api_key=API_KEY, base_url=BASE_URL, max_retries=0) as c, pytest.raises(InternalServerError):
        c.get_me()
    assert route.call_count == 1


def test_get_is_retried(client: Layrcake, api: respx.MockRouter) -> None:
    route = api.get("/v1/me").mock(side_effect=[httpx.Response(502), httpx.Response(200, json={"key": {}})])
    client.get_me()
    assert route.call_count == 2


def test_connection_errors_are_retried_with_the_same_key(
    client: Layrcake, api: respx.MockRouter, sleeps: List[float]
) -> None:
    route = api.post("/v1/verify_email").mock(
        side_effect=[httpx.ConnectError("refused"), httpx.ReadTimeout("slow"), httpx.Response(200, json=OK)]
    )
    client.verify_email(email="a@b.co")
    assert route.call_count == 3
    assert len(set(keys(route))) == 1
    assert len(sleeps) == 2


def test_timeout_raises_after_retries_with_the_key(client: Layrcake, api: respx.MockRouter) -> None:
    route = api.post("/v1/verify_email").mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(APITimeoutError) as info:
        client.verify_email(email="a@b.co")
    assert route.call_count == 3
    assert info.value.code == "timeout"
    assert info.value.idempotency_key == keys(route)[0]
    assert isinstance(info.value, APIConnectionError)


def test_connection_error_code(client: Layrcake, api: respx.MockRouter) -> None:
    api.get("/v1/me").mock(side_effect=httpx.ConnectError("refused"))
    with pytest.raises(APIConnectionError) as info:
        client.get_me()
    assert info.value.code == "connection_error"
    assert info.value.status is None
    assert info.value.idempotency_key is None
