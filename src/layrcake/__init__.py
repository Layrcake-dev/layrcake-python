"""The official Python SDK for the Layrcake API.

Go-to-market infrastructure for AI agents: find leads and companies, enrich
and verify contacts, and run email campaigns that a person approves.

    from layrcake import Layrcake

    client = Layrcake()  # reads LAYRCAKE_API_KEY
    leads = client.find_leads(audience="CTOs at US startups", titles=["CTO"], locations=["United States"], limit=10)
"""

from ._client import AsyncLayrcake, Layrcake
from ._exceptions import (
    APIConnectionError,
    APIResponseValidationError,
    APIStatusError,
    APITimeoutError,
    ApprovalError,
    AuthenticationError,
    BadRequestError,
    ConflictError,
    InsufficientCreditsError,
    InternalServerError,
    LayrcakeError,
    NotFoundError,
    OperationTimeoutError,
    PermissionDeniedError,
    RateLimitError,
)
from ._generated.operations import API_ERROR_CODES
from ._helpers import TERMINAL_OPERATION_STATES, ApprovalRequest, approval_required
from ._response import LayrcakeMeta, LayrcakeResponse
from ._types import NOT_GIVEN, NotGiven, Timeout
from ._version import __version__

__all__ = [
    "API_ERROR_CODES",
    "NOT_GIVEN",
    "TERMINAL_OPERATION_STATES",
    "APIConnectionError",
    "APIResponseValidationError",
    "APIStatusError",
    "APITimeoutError",
    "ApprovalError",
    "ApprovalRequest",
    "AsyncLayrcake",
    "AuthenticationError",
    "BadRequestError",
    "ConflictError",
    "InsufficientCreditsError",
    "InternalServerError",
    "Layrcake",
    "LayrcakeError",
    "LayrcakeMeta",
    "LayrcakeResponse",
    "NotFoundError",
    "NotGiven",
    "OperationTimeoutError",
    "PermissionDeniedError",
    "RateLimitError",
    "Timeout",
    "__version__",
    "approval_required",
]
