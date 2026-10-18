"""The static description of one API operation, used by the transport."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Tuple

__all__ = ["OperationSpec"]


@dataclass(frozen=True)
class OperationSpec:
    """Facts about one operation, generated from openapi.json."""

    name: str
    method: str
    path: str
    path_params: Tuple[Tuple[str, str], ...]
    """Pairs of (name in the path template, Python argument name)."""

    query_params: Tuple[str, ...]
    has_body: bool
    idempotency: Literal["none", "optional", "required"]
    """Whether the operation takes an Idempotency-Key header."""

    read_only: bool
    """True when the call never changes anything, so retrying it is always safe."""

    unwrap: bool
    """True when a `{result}` or `{result, layrcake}` body is unwrapped. False for flat bodies such as llm."""

    response_kind: Literal["json", "text", "empty"]
