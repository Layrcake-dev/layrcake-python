"""Small shared types."""

from __future__ import annotations

from typing import Optional, Union

import httpx

__all__ = ["NOT_GIVEN", "NotGiven", "Timeout"]


class NotGiven:
    """Marks an argument the caller did not pass.

    It differs from None, which some fields accept as a real value. Arguments
    left as NOT_GIVEN are not sent to the API at all.
    """

    _instance: Optional[NotGiven] = None

    def __new__(cls) -> NotGiven:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __bool__(self) -> bool:
        return False

    def __repr__(self) -> str:
        return "NOT_GIVEN"

    def __copy__(self) -> NotGiven:
        return self

    def __deepcopy__(self, memo: object) -> NotGiven:
        return self


NOT_GIVEN = NotGiven()
"""The value of every optional argument the caller leaves out."""

Timeout = Union[float, httpx.Timeout, None]
"""Seconds as a float, an httpx.Timeout, or None for no timeout."""
