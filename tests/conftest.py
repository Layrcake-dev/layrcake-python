from __future__ import annotations

from typing import Any, AsyncIterator, Dict, Iterator, List

import pytest
import respx

from layrcake import AsyncLayrcake, Layrcake

BASE_URL = "https://api.test"
API_KEY = "lc_test_key"


def envelope(result: Any, *, charged: str = "1.000000", available: str = "99.000000", **meta: Any) -> Dict[str, Any]:
    """A `{result, layrcake}` body as the API sends it."""
    return {
        "result": result,
        "layrcake": {
            "request_id": "11111111-1111-4111-8111-111111111111",
            "credits_charged": charged,
            "credits_available": available,
            **meta,
        },
    }


def error_body(code: str, message: str = "nope", **extra: Any) -> Dict[str, Any]:
    return {"error": {"code": code, "message": message, **extra}}


@pytest.fixture
def sleeps() -> List[float]:
    return []


@pytest.fixture
def api() -> Iterator[respx.MockRouter]:
    with respx.mock(base_url=BASE_URL, assert_all_called=False) as router:
        yield router


@pytest.fixture
def client(sleeps: List[float], api: respx.MockRouter) -> Iterator[Layrcake]:
    c = Layrcake(api_key=API_KEY, base_url=BASE_URL)
    c._transport._sleep = sleeps.append
    yield c
    c.close()


@pytest.fixture
async def async_client(sleeps: List[float], api: respx.MockRouter) -> AsyncIterator[AsyncLayrcake]:
    async def record(seconds: float) -> None:
        sleeps.append(seconds)

    c = AsyncLayrcake(api_key=API_KEY, base_url=BASE_URL)
    c._transport._sleep = record
    yield c
    await c.close()
