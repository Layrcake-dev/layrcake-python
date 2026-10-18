"""The Layrcake and AsyncLayrcake clients."""

from __future__ import annotations

import math
import os
import time
from types import TracebackType
from typing import Any, Dict, Mapping, Optional, Type, Union

import httpx

from ._base import DEFAULT_BASE_URL, DEFAULT_MAX_RETRIES, DEFAULT_TIMEOUT, AsyncTransport, SyncTransport
from ._exceptions import LayrcakeError, OperationTimeoutError
from ._generated.methods import AsyncMethods, SyncMethods
from ._generated.types import GetOperationResult
from ._helpers import TERMINAL_OPERATION_STATES
from ._types import NOT_GIVEN, NotGiven, Timeout

__all__ = ["AsyncLayrcake", "Layrcake"]

API_KEY_ENV = "LAYRCAKE_API_KEY"
BASE_URL_ENV = "LAYRCAKE_BASE_URL"


def _env(name: str) -> Optional[str]:
    value = os.environ.get(name)
    return value.strip() if value and value.strip() else None


def _resolve_api_key(api_key: Optional[str]) -> str:
    key = api_key.strip() if isinstance(api_key, str) else _env(API_KEY_ENV)
    if not key:
        raise LayrcakeError(
            "No Layrcake API key. Pass Layrcake(api_key=...) or set the "
            f"{API_KEY_ENV} environment variable. Get a key at https://layrcake.dev.",
            code="unauthorized",
        )
    return key


def _resolve_base_url(base_url: Optional[str]) -> str:
    url = base_url or _env(BASE_URL_ENV) or DEFAULT_BASE_URL
    if not url.startswith(("https://", "http://")):
        raise ValueError(f"base_url must start with https:// or http://, got {url!r}")
    return url


def _positive(value: float, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be a positive number of seconds")
    return float(value)


def _check_operation_id(operation_id: str) -> None:
    if not isinstance(operation_id, str) or not operation_id:
        raise ValueError("operation_id must be a non-empty string")


class Layrcake(SyncMethods):
    """The Layrcake API client.

    Example:
        from layrcake import Layrcake

        client = Layrcake()  # reads LAYRCAKE_API_KEY
        me = client.get_me()
        print(me.result["workspace"]["name"])
    """

    _transport: SyncTransport

    def __init__(
        self,
        api_key: Optional[str] = None,
        *,
        base_url: Optional[str] = None,
        max_retries: int = DEFAULT_MAX_RETRIES,
        timeout: Timeout = DEFAULT_TIMEOUT,
        http_client: Optional[httpx.Client] = None,
        default_headers: Optional[Mapping[str, str]] = None,
    ) -> None:
        """Create a client.

        Args:
            api_key: Your API key. Defaults to the LAYRCAKE_API_KEY environment variable.
            base_url: Defaults to LAYRCAKE_BASE_URL, then https://api.layrcake.dev.
            max_retries: Automatic retries for transient failures. Default 2.
            timeout: Seconds per attempt, an httpx.Timeout, or None for no limit. Default 60.
            http_client: Your own httpx.Client, for proxies or custom transports. You close it.
            default_headers: Headers sent with every request.
        """
        self._transport = SyncTransport(
            http_client=http_client,
            api_key=_resolve_api_key(api_key),
            base_url=_resolve_base_url(base_url),
            max_retries=max_retries,
            timeout=timeout,
            default_headers=default_headers,
        )

    @property
    def api_key(self) -> str:
        return self._transport.api_key

    @property
    def base_url(self) -> str:
        return self._transport.base_url

    @property
    def max_retries(self) -> int:
        return self._transport.max_retries

    @property
    def timeout(self) -> Timeout:
        return self._transport.timeout

    def with_options(
        self,
        *,
        max_retries: Union[int, NotGiven] = NOT_GIVEN,
        timeout: Union[Timeout, NotGiven] = NOT_GIVEN,
        default_headers: Union[Mapping[str, str], NotGiven] = NOT_GIVEN,
    ) -> Layrcake:
        """A copy of this client with some settings changed. It shares the connection pool.

        Example:
            client.with_options(max_retries=0, timeout=5).get_me()
        """
        copy = Layrcake.__new__(Layrcake)
        copy._transport = SyncTransport(
            http_client=self._transport.client,
            **_copied_settings(self._transport, max_retries, timeout, default_headers),
        )
        return copy

    def wait_for_operation(
        self,
        operation_id: str,
        interval: float = 2.0,
        timeout: float = 600.0,
    ) -> GetOperationResult:
        """Poll get_operation until the operation reaches a terminal state, then return it.

        Terminal states are succeeded, partially_succeeded, failed, cancelled
        and unknown. It does not raise on failed: check `operation["state"]`.
        `unknown` means a provider's outcome could not be confirmed and
        Layrcake is reconciling it. Do not repeat the action while it is unknown.

        Args:
            operation_id: The operation_id a call returned.
            interval: Seconds between polls. Default 2.
            timeout: Seconds to wait in total. Default 600. Then OperationTimeoutError is raised.
        """
        _check_operation_id(operation_id)
        interval = _positive(interval, "interval")
        timeout = _positive(timeout, "timeout")
        deadline = time.monotonic() + timeout
        while True:
            operation = self.get_operation(operation_id).result
            if operation.get("state") in TERMINAL_OPERATION_STATES:
                return operation
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise OperationTimeoutError(operation, timeout)
            self._transport._sleep(min(interval, remaining))

    def close(self) -> None:
        """Close the connection pool. A client you passed in stays open."""
        self._transport.close()

    def __enter__(self) -> Layrcake:
        return self

    def __exit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc: Optional[BaseException],
        tb: Optional[TracebackType],
    ) -> None:
        self.close()

    def __repr__(self) -> str:
        return f"Layrcake(base_url={self.base_url!r}, max_retries={self.max_retries!r})"


class AsyncLayrcake(AsyncMethods):
    """The Layrcake API client for asyncio. Every API method is a coroutine.

    Example:
        import asyncio
        from layrcake import AsyncLayrcake

        async def main() -> None:
            async with AsyncLayrcake() as client:
                me = await client.get_me()
                print(me.result["workspace"]["name"])

        asyncio.run(main())
    """

    _transport: AsyncTransport

    def __init__(
        self,
        api_key: Optional[str] = None,
        *,
        base_url: Optional[str] = None,
        max_retries: int = DEFAULT_MAX_RETRIES,
        timeout: Timeout = DEFAULT_TIMEOUT,
        http_client: Optional[httpx.AsyncClient] = None,
        default_headers: Optional[Mapping[str, str]] = None,
    ) -> None:
        """Create a client. The arguments match Layrcake, with an httpx.AsyncClient."""
        self._transport = AsyncTransport(
            http_client=http_client,
            api_key=_resolve_api_key(api_key),
            base_url=_resolve_base_url(base_url),
            max_retries=max_retries,
            timeout=timeout,
            default_headers=default_headers,
        )

    @property
    def api_key(self) -> str:
        return self._transport.api_key

    @property
    def base_url(self) -> str:
        return self._transport.base_url

    @property
    def max_retries(self) -> int:
        return self._transport.max_retries

    @property
    def timeout(self) -> Timeout:
        return self._transport.timeout

    def with_options(
        self,
        *,
        max_retries: Union[int, NotGiven] = NOT_GIVEN,
        timeout: Union[Timeout, NotGiven] = NOT_GIVEN,
        default_headers: Union[Mapping[str, str], NotGiven] = NOT_GIVEN,
    ) -> AsyncLayrcake:
        """A copy of this client with some settings changed. It shares the connection pool."""
        copy = AsyncLayrcake.__new__(AsyncLayrcake)
        copy._transport = AsyncTransport(
            http_client=self._transport.client,
            **_copied_settings(self._transport, max_retries, timeout, default_headers),
        )
        return copy

    async def wait_for_operation(
        self,
        operation_id: str,
        interval: float = 2.0,
        timeout: float = 600.0,
    ) -> GetOperationResult:
        """Poll get_operation until the operation reaches a terminal state. See Layrcake.wait_for_operation."""
        _check_operation_id(operation_id)
        interval = _positive(interval, "interval")
        timeout = _positive(timeout, "timeout")
        deadline = time.monotonic() + timeout
        while True:
            operation = (await self.get_operation(operation_id)).result
            if operation.get("state") in TERMINAL_OPERATION_STATES:
                return operation
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise OperationTimeoutError(operation, timeout)
            await self._transport._sleep(min(interval, remaining))

    async def close(self) -> None:
        """Close the connection pool. A client you passed in stays open."""
        await self._transport.close()

    async def __aenter__(self) -> AsyncLayrcake:
        return self

    async def __aexit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc: Optional[BaseException],
        tb: Optional[TracebackType],
    ) -> None:
        await self.close()

    def __repr__(self) -> str:
        return f"AsyncLayrcake(base_url={self.base_url!r}, max_retries={self.max_retries!r})"


def _copied_settings(
    transport: Union[SyncTransport, AsyncTransport],
    max_retries: Union[int, NotGiven],
    timeout: Union[Timeout, NotGiven],
    default_headers: Union[Mapping[str, str], NotGiven],
) -> Dict[str, Any]:
    return {
        "api_key": transport.api_key,
        "base_url": transport.base_url,
        "max_retries": transport.max_retries if isinstance(max_retries, NotGiven) else max_retries,
        "timeout": transport.timeout if isinstance(timeout, NotGiven) else timeout,
        "default_headers": transport.default_headers if isinstance(default_headers, NotGiven) else default_headers,
    }
