"""The object every API method returns."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Generic, Mapping, Optional, TypeVar

import httpx

__all__ = ["LayrcakeMeta", "LayrcakeResponse"]

T = TypeVar("T", covariant=True)


def _credits(value: Any) -> Decimal:
    """Parse a credit amount. The API sends credits as decimal strings."""
    if isinstance(value, bool) or value is None:
        raise ValueError(f"not a credit amount: {value!r}")
    if isinstance(value, float):
        # Never let binary floating point into a balance. A float here means
        # the server changed type, so keep its shortest exact repr.
        value = repr(value)
    try:
        return Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError(f"not a credit amount: {value!r}") from exc


@dataclass(frozen=True)
class LayrcakeMeta:
    """The `layrcake` block of a response: billing and tracing for one call."""

    request_id: str
    """The id of this request. Quote it when you contact support."""

    credits_charged: Decimal
    """Credits this call cost. Exact, never a float."""

    credits_available: Decimal
    """Credits left in the workspace after this call."""

    replayed: bool = False
    """True when the answer was replayed from an earlier call with the same idempotency key."""

    route: Optional[Mapping[str, Any]] = None
    """Which source preference was used and where it came from, when reported."""

    raw: Mapping[str, Any] = field(default_factory=dict, repr=False)
    """The block exactly as the API sent it, with credits as strings."""

    @classmethod
    def from_wire(cls, data: Mapping[str, Any]) -> LayrcakeMeta:
        route = data.get("route")
        return cls(
            request_id=str(data.get("request_id", "")),
            credits_charged=_credits(data.get("credits_charged", "0")),
            credits_available=_credits(data.get("credits_available", "0")),
            replayed=bool(data.get("replayed", False)),
            route=route if isinstance(route, Mapping) else None,
            raw=dict(data),
        )


@dataclass(frozen=True)
class LayrcakeResponse(Generic[T]):
    """A successful API response.

    `result` is what you asked for. For calls that answer with
    `{result, layrcake}` it is the `result` part. For calls that answer with a
    bare object, such as get_me and get_operation, it is the whole body.
    """

    result: T
    """The answer to the call, typed per operation."""

    layrcake: Optional[LayrcakeMeta]
    """Billing and tracing for this call. None when the API sent no `layrcake` block."""

    request_id: Optional[str]
    """The request id from the `layrcake` block or the response headers."""

    status_code: int
    """The HTTP status. 200, or 202 when a person has already approved the action."""

    headers: httpx.Headers = field(repr=False)
    """The HTTP response headers."""

    body: Any = field(repr=False)
    """The parsed response body, unchanged."""

    @property
    def credits_charged(self) -> Optional[Decimal]:
        """Shortcut for `layrcake.credits_charged`. None when the call reported no billing."""
        return self.layrcake.credits_charged if self.layrcake is not None else None
