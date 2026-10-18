"""Exceptions raised by the SDK.

Every exception derives from LayrcakeError, which carries `status`, `code`,
`message`, `details`, `request_id` and `idempotency_key`. Catch a subclass to
handle one kind of failure, or branch on `code` for detail.
"""

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional, Type

import httpx

__all__ = [
    "APIConnectionError",
    "APIResponseValidationError",
    "APIStatusError",
    "APITimeoutError",
    "ApprovalError",
    "AuthenticationError",
    "BadRequestError",
    "ConflictError",
    "InsufficientCreditsError",
    "InternalServerError",
    "LayrcakeError",
    "NotFoundError",
    "OperationTimeoutError",
    "PermissionDeniedError",
    "RateLimitError",
]


class LayrcakeError(Exception):
    """Base class for every error the SDK raises."""

    status: Optional[int]
    """The HTTP status, or None when no response arrived."""

    code: str
    """A machine-readable code, such as "insufficient_credits". Branch on this.

    API codes come from the API (see API_ERROR_CODES). The SDK adds its own
    when there is no API answer: "connection_error", "timeout",
    "invalid_response" and "operation_timeout". An error response without a
    JSON body gets the usual code for its status, or "http_<status>".
    """

    message: str
    """A plain description of what went wrong."""

    details: Dict[str, Any]
    """Extra facts from the API, such as reason or retry_after_seconds. Empty when there are none."""

    request_id: Optional[str]
    """The API's request id. Quote it to support."""

    idempotency_key: Optional[str]
    """The Idempotency-Key the SDK sent.

    After a timeout or a connection error, retry with this key to get the
    original outcome without paying twice.
    """

    retry_after_seconds: Optional[float]
    """How long the API asked you to wait before retrying, when it said."""

    headers: Optional[httpx.Headers]
    """The response headers, when a response arrived."""

    def __init__(
        self,
        message: str,
        *,
        code: str,
        status: Optional[int] = None,
        details: Optional[Mapping[str, Any]] = None,
        request_id: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        retry_after_seconds: Optional[float] = None,
        headers: Optional[httpx.Headers] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.status = status
        self.details = dict(details or {})
        self.request_id = request_id
        self.idempotency_key = idempotency_key
        self.retry_after_seconds = retry_after_seconds
        self.headers = headers

    def __str__(self) -> str:
        head = self.code if self.status is None else f"{self.code} (HTTP {self.status})"
        text = f"{head}: {self.message}"
        if self.request_id:
            text += f" [request_id: {self.request_id}]"
        return text

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(code={self.code!r}, status={self.status!r}, "
            f"message={self.message!r}, request_id={self.request_id!r})"
        )


class APIConnectionError(LayrcakeError):
    """No response arrived: DNS, TLS, a reset connection or a dropped read."""

    def __init__(
        self,
        message: str = "Connection error.",
        *,
        code: str = "connection_error",
        idempotency_key: Optional[str] = None,
        request: Optional[httpx.Request] = None,
    ) -> None:
        super().__init__(message, code=code, idempotency_key=idempotency_key)
        self.request = request


class APITimeoutError(APIConnectionError):
    """The request took longer than the timeout."""

    def __init__(
        self,
        message: str = "Request timed out.",
        *,
        idempotency_key: Optional[str] = None,
        request: Optional[httpx.Request] = None,
    ) -> None:
        super().__init__(message, code="timeout", idempotency_key=idempotency_key, request=request)


class APIResponseValidationError(LayrcakeError):
    """The API answered with a success status, but the SDK cannot read the body."""

    def __init__(
        self,
        message: str,
        *,
        response: httpx.Response,
        request_id: Optional[str] = None,
        idempotency_key: Optional[str] = None,
    ) -> None:
        super().__init__(
            message,
            code="invalid_response",
            status=response.status_code,
            request_id=request_id,
            idempotency_key=idempotency_key,
            headers=response.headers,
        )
        self.response = response


class APIStatusError(LayrcakeError):
    """The API answered with an error status. Base class for the HTTP errors below."""

    status: int

    def __init__(
        self,
        message: str,
        *,
        response: httpx.Response,
        code: str,
        details: Optional[Mapping[str, Any]] = None,
        request_id: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        retry_after_seconds: Optional[float] = None,
        body: Any = None,
    ) -> None:
        super().__init__(
            message,
            code=code,
            status=response.status_code,
            details=details,
            request_id=request_id,
            idempotency_key=idempotency_key,
            retry_after_seconds=retry_after_seconds,
            headers=response.headers,
        )
        self.response = response
        self.body = body


class BadRequestError(APIStatusError):
    """400 or 422. The request is invalid. `details` usually names the field."""


class AuthenticationError(APIStatusError):
    """401. The API key is missing, wrong, revoked or expired."""


class InsufficientCreditsError(APIStatusError):
    """402, or a run budget reached. Top up credits or raise the budget."""


class PermissionDeniedError(APIStatusError):
    """403. The key may not do this: a missing scope, a paused agent or a disabled feature."""


class ApprovalError(APIStatusError):
    """A person has not approved this action, or the approval no longer stands.

    Raised for confirmation_required and for an approval that was declined,
    expired, superseded, cancelled or changed. The SDK never approves anything.
    Send a person to the approval URL from the preview response.
    """

    @property
    def approval_url(self) -> Optional[str]:
        """Where a person can approve, when the API included it in `details`."""
        confirmation = self.details.get("confirmation")
        if isinstance(confirmation, Mapping):
            url = confirmation.get("approval_url")
            if isinstance(url, str) and url:
                return url
        url = self.details.get("approval_url")
        return url if isinstance(url, str) and url else None


class NotFoundError(APIStatusError):
    """404. The resource does not exist in this workspace, or the feature is off."""


class ConflictError(APIStatusError):
    """409. A conflicting state, such as an idempotency key reused with different parameters."""


class RateLimitError(APIStatusError):
    """429. Too many requests. `retry_after_seconds` says when to try again."""


class InternalServerError(APIStatusError):
    """5xx. The API or an upstream provider failed, such as provider_unavailable."""


class OperationTimeoutError(LayrcakeError):
    """wait_for_operation ran out of time.

    The operation may still finish. `operation` is the last state seen. Call
    wait_for_operation again to keep waiting.
    """

    def __init__(self, operation: Mapping[str, Any], timeout: float) -> None:
        operation_id = str(operation.get("operation_id", ""))
        state = operation.get("state")
        super().__init__(
            f"Operation {operation_id} was still {state} after {timeout:g} seconds",
            code="operation_timeout",
        )
        self.operation: Dict[str, Any] = dict(operation)
        self.operation_id = operation_id


_CLASS_BY_CODE: Dict[str, Type[APIStatusError]] = {
    "unauthorized": AuthenticationError,
    "confirmation_required": ApprovalError,
    "approval_declined": ApprovalError,
    "approval_expired": ApprovalError,
    "approval_superseded": ApprovalError,
    "approval_cancelled": ApprovalError,
    "approval_changed": ApprovalError,
    "payment_required": InsufficientCreditsError,
    "insufficient_credits": InsufficientCreditsError,
    "card_required": InsufficientCreditsError,
    "payment_action_required": InsufficientCreditsError,
    "run_budget_exceeded": InsufficientCreditsError,
    "invalid_request": BadRequestError,
    "suppressed_recipient": BadRequestError,
    "not_found": NotFoundError,
    "rate_limited": RateLimitError,
}

_CODE_BY_STATUS: Dict[int, str] = {
    400: "invalid_request",
    401: "unauthorized",
    402: "payment_required",
    403: "forbidden",
    404: "not_found",
    422: "invalid_request",
    429: "rate_limited",
    500: "internal",
}


def _class_for_status(status: int) -> Type[APIStatusError]:
    if status in (400, 422):
        return BadRequestError
    if status == 401:
        return AuthenticationError
    if status == 402:
        return InsufficientCreditsError
    if status == 403:
        return PermissionDeniedError
    if status == 404:
        return NotFoundError
    if status == 409:
        return ConflictError
    if status == 429:
        return RateLimitError
    if status >= 500:
        return InternalServerError
    return APIStatusError


def error_class_for(status: int, code: str) -> Type[APIStatusError]:
    """The exception class for an error code, falling back to its HTTP status."""
    return _CLASS_BY_CODE.get(code) or _class_for_status(status)


def default_code_for(status: int) -> str:
    """The code used when an error response carries none."""
    return _CODE_BY_STATUS.get(status, f"http_{status}")
