from __future__ import annotations

from typing import Any, Dict, Optional, Type

import pytest
import respx

from layrcake import (
    API_ERROR_CODES,
    APIStatusError,
    ApprovalError,
    AuthenticationError,
    BadRequestError,
    ConflictError,
    InsufficientCreditsError,
    InternalServerError,
    Layrcake,
    LayrcakeError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
)

from .conftest import error_body


@pytest.mark.parametrize(
    ("status", "code", "expected"),
    [
        (400, "invalid_request", BadRequestError),
        (422, "suppressed_recipient", BadRequestError),
        (401, "unauthorized", AuthenticationError),
        (402, "insufficient_credits", InsufficientCreditsError),
        (402, "card_required", InsufficientCreditsError),
        (402, "payment_action_required", InsufficientCreditsError),
        (403, "run_budget_exceeded", InsufficientCreditsError),
        (403, "forbidden", PermissionDeniedError),
        (403, "agent_paused", PermissionDeniedError),
        (403, "confirmation_required", ApprovalError),
        (409, "approval_expired", ApprovalError),
        (409, "approval_declined", ApprovalError),
        (404, "not_found", NotFoundError),
        (409, "idempotency_conflict", ConflictError),
        (409, "reply_already_sent", ConflictError),
        (429, "rate_limited", RateLimitError),
        (500, "internal", InternalServerError),
        (503, "provider_unavailable", InternalServerError),
        (418, "brand_new_code", APIStatusError),
    ],
)
def test_errors_map_to_classes(api: respx.MockRouter, status: int, code: str, expected: Type[APIStatusError]) -> None:
    api.post("/v1/verify_email").respond(
        status,
        json=error_body(code, "what went wrong", request_id="req_err", details={"reason": "r1"}),
    )
    with Layrcake(api_key="k", base_url="https://api.test", max_retries=0) as client, pytest.raises(expected) as info:
        client.verify_email(email="a@b.co", idempotency_key="key-1")
    error = info.value
    assert type(error) is expected
    assert isinstance(error, LayrcakeError)
    assert error.status == status
    assert error.code == code
    assert error.message == "what went wrong"
    assert error.details == {"reason": "r1"}
    assert error.request_id == "req_err"
    assert error.idempotency_key == "key-1"
    assert error.headers is not None
    assert str(error) == f"{code} (HTTP {status}): what went wrong [request_id: req_err]"


@pytest.mark.parametrize(
    ("status", "body", "code", "message"),
    [
        (401, None, "unauthorized", "HTTP 401"),
        (404, "Not Found", "not_found", "HTTP 404: Not Found"),
        (502, "<html><body>Bad gateway</body></html>", "http_502", "HTTP 502"),
        (409, {"unexpected": True}, "http_409", "HTTP 409"),
    ],
)
def test_error_without_api_body_gets_a_code(
    client: Layrcake, api: respx.MockRouter, status: int, body: Optional[Any], code: str, message: str
) -> None:
    kwargs: Dict[str, Any] = {}
    if isinstance(body, str):
        kwargs["text"] = body
    elif body is not None:
        kwargs["json"] = body
    api.get("/v1/me").respond(status, headers={"X-Request-Id": "req_hdr"}, **kwargs)
    with pytest.raises(APIStatusError) as info:
        client.with_options(max_retries=0).get_me()
    assert info.value.code == code
    assert info.value.message == message
    assert info.value.request_id == "req_hdr"


def test_approval_error_exposes_the_approval_url(client: Layrcake, api: respx.MockRouter) -> None:
    api.post("/v1/reply_to_thread").respond(
        403,
        json=error_body(
            "confirmation_required",
            "a human must approve this reply in the console before it is sent",
            details={"confirmation": {"approval_url": "https://app.layrcake.dev/approve/x"}},
        ),
    )
    with pytest.raises(ApprovalError) as info:
        client.reply_to_thread(reply_id="r1", body_text="Thanks", confirmation_token="tok")
    assert info.value.approval_url == "https://app.layrcake.dev/approve/x"
    assert isinstance(info.value, APIStatusError)


def test_error_codes_come_from_the_spec() -> None:
    assert "insufficient_credits" in API_ERROR_CODES
    assert "confirmation_required" in API_ERROR_CODES
    assert len(API_ERROR_CODES) == len(set(API_ERROR_CODES))
