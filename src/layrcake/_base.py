"""HTTP transport: request building, retries, response and error parsing.

The sync and async transports share every decision through _BaseTransport.
Only sending and sleeping differ between them.
"""

from __future__ import annotations

import asyncio
import email.utils
import json
import logging
import math
import platform
import random
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, Mapping, Optional, Union
from urllib.parse import quote

import httpx

from ._exceptions import (
    APIConnectionError,
    APIResponseValidationError,
    APIStatusError,
    APITimeoutError,
    default_code_for,
    error_class_for,
)
from ._operation import OperationSpec
from ._response import LayrcakeMeta, LayrcakeResponse
from ._types import NOT_GIVEN, NotGiven, Timeout
from ._version import __version__

__all__ = ["AsyncTransport", "SyncTransport", "retry_after_seconds"]

log = logging.getLogger("layrcake")

DEFAULT_BASE_URL = "https://api.layrcake.dev"
DEFAULT_TIMEOUT = 60.0
DEFAULT_MAX_RETRIES = 2

RETRY_STATUSES = frozenset({429, 502, 503, 504})
"""Statuses worth retrying. Every other 4xx is final."""

INITIAL_RETRY_DELAY = 0.5
MAX_RETRY_DELAY = 8.0
MAX_RETRY_AFTER = 60.0
"""A server asking for a longer wait than this is not retried automatically. The error is raised."""

REQUEST_ID_HEADERS = ("x-layrcake-request-id", "x-request-id")


def retry_after_seconds(
    response: Optional[httpx.Response], details: Optional[Mapping[str, Any]] = None
) -> Optional[float]:
    """Seconds the server asked the client to wait, or None when it did not say.

    Reads Retry-After-Ms, then Retry-After (seconds or an HTTP date), then the
    error body's details.retry_after_seconds.
    """
    if response is not None:
        for name, scale in (("retry-after-ms", 0.001), ("retry-after", 1.0)):
            header = response.headers.get(name)
            if header is None:
                continue
            try:
                value = float(header) * scale
            except ValueError:
                parsed = email.utils.parsedate_tz(header) if scale == 1.0 else None
                if parsed is not None:
                    return max(0.0, email.utils.mktime_tz(parsed) - time.time())
                continue
            if not math.isnan(value):
                return max(0.0, value)
    if details is not None:
        hint = details.get("retry_after_seconds")
        if isinstance(hint, (int, float)) and not isinstance(hint, bool) and not math.isnan(hint):
            return max(0.0, float(hint))
    return None


@dataclass
class _Prepared:
    request: httpx.Request
    idempotency_key: Optional[str]
    retry_safe: bool
    """Whether repeating the call cannot act twice: a read, or a write under an idempotency key."""


def _strip(mapping: Mapping[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in mapping.items() if not isinstance(v, NotGiven)}


class _BaseTransport:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        max_retries: int,
        timeout: Timeout,
        default_headers: Optional[Mapping[str, str]],
    ) -> None:
        if not isinstance(max_retries, int) or isinstance(max_retries, bool) or max_retries < 0:
            raise ValueError("max_retries must be a whole number, 0 or more")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.max_retries = max_retries
        self.timeout = timeout
        self.default_headers: Dict[str, str] = dict(default_headers or {})

    # Request building.

    def _prepare(
        self,
        client: Union[httpx.Client, httpx.AsyncClient],
        spec: OperationSpec,
        *,
        path_params: Optional[Mapping[str, str]],
        query: Optional[Mapping[str, Any]],
        body: Optional[Mapping[str, Any]],
        extra_body: Optional[Mapping[str, Any]],
        idempotency_key: Union[str, NotGiven],
        extra_headers: Optional[Mapping[str, str]],
        extra_query: Optional[Mapping[str, Any]],
        timeout: Union[Timeout, NotGiven],
    ) -> _Prepared:
        path = spec.path
        for template_name, arg_name in spec.path_params:
            value = (path_params or {}).get(template_name)
            if not isinstance(value, str) or not value:
                raise ValueError(f"{spec.name} needs a non-empty {arg_name}")
            path = path.replace("{" + template_name + "}", quote(value, safe=""))

        headers: Dict[str, str] = {
            "Accept": "text/csv, application/json" if spec.response_kind == "text" else "application/json",
            "User-Agent": f"layrcake-python/{__version__} (Python {platform.python_version()})",
            **self.default_headers,
            **(extra_headers or {}),
        }

        key: Optional[str] = None
        if spec.idempotency != "none":
            if isinstance(idempotency_key, NotGiven):
                # One key per logical call, reused by every retry below, so the
                # API returns the first outcome instead of acting or charging twice.
                key = str(uuid.uuid4())
            elif isinstance(idempotency_key, str) and idempotency_key:
                key = idempotency_key
            else:
                raise ValueError("idempotency_key must be a non-empty string")
            headers["Idempotency-Key"] = key

        content: Optional[bytes] = None
        if spec.has_body:
            payload = _strip(body or {})
            if extra_body:
                payload.update(extra_body)
            content = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"

        params = {k: v for k, v in _strip(query or {}).items() if v is not None}
        if extra_query:
            params.update(extra_query)

        # Set last so no header option can replace the key.
        headers["Authorization"] = f"Bearer {self.api_key}"

        request = client.build_request(
            spec.method,
            self.base_url + path,
            params=params or None,
            content=content,
            headers=headers,
            timeout=self.timeout if isinstance(timeout, NotGiven) else timeout,
        )
        safe = spec.method in ("GET", "HEAD") or spec.read_only or key is not None
        return _Prepared(request=request, idempotency_key=key, retry_safe=safe)

    # Retry decisions.

    def _backoff(self, attempt: int) -> float:
        delay = min(INITIAL_RETRY_DELAY * (2.0**attempt), MAX_RETRY_DELAY)
        return delay * (1.0 - 0.25 * random.random())

    def _status_retry_delay(self, attempt: int, prepared: _Prepared, error: APIStatusError) -> Optional[float]:
        """Seconds to wait before retrying after an error status, or None to raise it."""
        if attempt >= self.max_retries or error.status not in RETRY_STATUSES:
            return None
        # A 429 was refused before any work ran, so repeating it is always
        # safe. A 5xx may have done work, so it needs a repeat-safe call.
        if error.status != 429 and not prepared.retry_safe:
            return None
        if error.retry_after_seconds is not None:
            return error.retry_after_seconds if error.retry_after_seconds <= MAX_RETRY_AFTER else None
        return self._backoff(attempt)

    def _transport_retry_delay(self, attempt: int, prepared: _Prepared, exc: httpx.TransportError) -> Optional[float]:
        """Seconds to wait before retrying after a network failure, or None to raise it."""
        if attempt >= self.max_retries:
            return None
        # A failed connect never reached the server, so it is safe to repeat.
        never_sent = isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout))
        if not (never_sent or prepared.retry_safe):
            return None
        return self._backoff(attempt)

    # Response parsing.

    def _parse(
        self, spec: OperationSpec, response: httpx.Response, idempotency_key: Optional[str]
    ) -> LayrcakeResponse[Any]:
        header_request_id = _header_request_id(response)
        if spec.response_kind == "text":
            return LayrcakeResponse(
                result=response.text,
                layrcake=None,
                request_id=header_request_id,
                status_code=response.status_code,
                headers=response.headers,
                body=response.text,
            )

        body: Any = None
        if response.content:
            try:
                body = response.json()
            except ValueError as exc:
                raise APIResponseValidationError(
                    f"Expected JSON from {spec.name}, got: {response.text[:120]}",
                    response=response,
                    request_id=header_request_id,
                    idempotency_key=idempotency_key,
                ) from exc

        result: Any = body
        meta: Optional[LayrcakeMeta] = None
        # Unwrap by shape, not by what the spec says: the live API wraps some
        # bodies the spec describes as flat. A 202 {status, approval} body has
        # other keys, so it stays whole.
        if spec.unwrap and _is_envelope(body):
            result = body["result"]
            raw_meta = body.get("layrcake")
            if isinstance(raw_meta, dict):
                try:
                    meta = LayrcakeMeta.from_wire(raw_meta)
                except ValueError as exc:
                    raise APIResponseValidationError(
                        f"{spec.name}: the layrcake block has a credit amount that is not a decimal",
                        response=response,
                        request_id=header_request_id,
                        idempotency_key=idempotency_key,
                    ) from exc

        body_request_id = body.get("request_id") if isinstance(body, dict) else None
        request_id = (
            (meta.request_id if meta is not None and meta.request_id else None)
            or header_request_id
            or (body_request_id if isinstance(body_request_id, str) else None)
        )
        return LayrcakeResponse(
            result=result,
            layrcake=meta,
            request_id=request_id,
            status_code=response.status_code,
            headers=response.headers,
            body=body,
        )

    def _connection_error(self, exc: httpx.TransportError, prepared: _Prepared) -> APIConnectionError:
        if isinstance(exc, httpx.TimeoutException):
            return APITimeoutError(
                f"Request timed out ({type(exc).__name__})",
                idempotency_key=prepared.idempotency_key,
                request=prepared.request,
            )
        return APIConnectionError(
            f"Connection error: {type(exc).__name__}: {exc}",
            idempotency_key=prepared.idempotency_key,
            request=prepared.request,
        )


def _is_envelope(body: Any) -> bool:
    """True for a JSON object with `result` and no keys other than result and layrcake."""
    return isinstance(body, dict) and "result" in body and all(k in ("result", "layrcake") for k in body)


def _header_request_id(response: httpx.Response) -> Optional[str]:
    for name in REQUEST_ID_HEADERS:
        value = response.headers.get(name)
        if isinstance(value, str) and value:
            return value
    return None


@dataclass
class _ErrorFields:
    code: Optional[str] = None
    message: Optional[str] = None
    details: Dict[str, Any] = field(default_factory=dict)
    request_id: Optional[str] = None
    body: Any = None


def _error_fields(response: httpx.Response) -> _ErrorFields:
    """Read the API's error body: `{"error": {code, message, request_id, details}}`."""
    try:
        body: Any = response.json()
    except ValueError:
        return _ErrorFields(body=response.text)
    error = body.get("error") if isinstance(body, dict) else None
    if not isinstance(error, dict):
        return _ErrorFields(body=body)
    code = error.get("code")
    message = error.get("message")
    details = error.get("details")
    request_id = error.get("request_id")
    return _ErrorFields(
        code=code if isinstance(code, str) and code else None,
        message=message if isinstance(message, str) and message else None,
        details=dict(details) if isinstance(details, dict) else {},
        request_id=request_id if isinstance(request_id, str) and request_id else None,
        body=body,
    )


def make_status_error(response: httpx.Response, idempotency_key: Optional[str] = None) -> APIStatusError:
    """Build the right exception for an error response."""
    fields = _error_fields(response)
    code = fields.code or default_code_for(response.status_code)
    message = fields.message
    if message is None:
        message = f"HTTP {response.status_code}"
        text = fields.body.strip() if isinstance(fields.body, str) else ""
        # Keep a short plain-text reason, but not an HTML error page.
        if text and not text.startswith("<"):
            message += f": {text[:200]}"
    cls = error_class_for(response.status_code, code)
    return cls(
        message,
        response=response,
        code=code,
        details=fields.details,
        request_id=fields.request_id or _header_request_id(response),
        idempotency_key=idempotency_key,
        retry_after_seconds=retry_after_seconds(response, fields.details),
        body=fields.body,
    )


class SyncTransport(_BaseTransport):
    def __init__(self, *, http_client: Optional[httpx.Client], **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._owns_client = http_client is None
        self.client = http_client if http_client is not None else httpx.Client(timeout=self.timeout)
        self._sleep: Callable[[float], None] = time.sleep

    def request(
        self,
        spec: OperationSpec,
        *,
        path_params: Optional[Mapping[str, str]] = None,
        query: Optional[Mapping[str, Any]] = None,
        body: Optional[Mapping[str, Any]] = None,
        extra_body: Optional[Mapping[str, Any]] = None,
        idempotency_key: Union[str, NotGiven] = NOT_GIVEN,
        extra_headers: Optional[Mapping[str, str]] = None,
        extra_query: Optional[Mapping[str, Any]] = None,
        timeout: Union[Timeout, NotGiven] = NOT_GIVEN,
    ) -> LayrcakeResponse[Any]:
        prepared = self._prepare(
            self.client,
            spec,
            path_params=path_params,
            query=query,
            body=body,
            extra_body=extra_body,
            idempotency_key=idempotency_key,
            extra_headers=extra_headers,
            extra_query=extra_query,
            timeout=timeout,
        )
        attempt = 0
        while True:
            try:
                response = self.client.send(prepared.request)
            except httpx.TransportError as exc:
                delay = self._transport_retry_delay(attempt, prepared, exc)
                if delay is None:
                    raise self._connection_error(exc, prepared) from exc
                log.debug("layrcake %s: %s, retry %d in %.2fs", spec.name, type(exc).__name__, attempt + 1, delay)
            else:
                if response.is_success:
                    return self._parse(spec, response, prepared.idempotency_key)
                error = make_status_error(response, prepared.idempotency_key)
                delay = self._status_retry_delay(attempt, prepared, error)
                if delay is None:
                    raise error
                log.debug("layrcake %s: HTTP %s, retry %d in %.2fs", spec.name, error.status, attempt + 1, delay)
            self._sleep(delay)
            attempt += 1

    def close(self) -> None:
        if self._owns_client:
            self.client.close()


class AsyncTransport(_BaseTransport):
    def __init__(self, *, http_client: Optional[httpx.AsyncClient], **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._owns_client = http_client is None
        self.client = http_client if http_client is not None else httpx.AsyncClient(timeout=self.timeout)
        self._sleep: Callable[[float], Awaitable[None]] = asyncio.sleep

    async def request(
        self,
        spec: OperationSpec,
        *,
        path_params: Optional[Mapping[str, str]] = None,
        query: Optional[Mapping[str, Any]] = None,
        body: Optional[Mapping[str, Any]] = None,
        extra_body: Optional[Mapping[str, Any]] = None,
        idempotency_key: Union[str, NotGiven] = NOT_GIVEN,
        extra_headers: Optional[Mapping[str, str]] = None,
        extra_query: Optional[Mapping[str, Any]] = None,
        timeout: Union[Timeout, NotGiven] = NOT_GIVEN,
    ) -> LayrcakeResponse[Any]:
        prepared = self._prepare(
            self.client,
            spec,
            path_params=path_params,
            query=query,
            body=body,
            extra_body=extra_body,
            idempotency_key=idempotency_key,
            extra_headers=extra_headers,
            extra_query=extra_query,
            timeout=timeout,
        )
        attempt = 0
        while True:
            try:
                response = await self.client.send(prepared.request)
            except httpx.TransportError as exc:
                delay = self._transport_retry_delay(attempt, prepared, exc)
                if delay is None:
                    raise self._connection_error(exc, prepared) from exc
                log.debug("layrcake %s: %s, retry %d in %.2fs", spec.name, type(exc).__name__, attempt + 1, delay)
            else:
                if response.is_success:
                    return self._parse(spec, response, prepared.idempotency_key)
                error = make_status_error(response, prepared.idempotency_key)
                delay = self._status_retry_delay(attempt, prepared, error)
                if delay is None:
                    raise error
                log.debug("layrcake %s: HTTP %s, retry %d in %.2fs", spec.name, error.status, attempt + 1, delay)
            await self._sleep(delay)
            attempt += 1

    async def close(self) -> None:
        if self._owns_client:
            await self.client.aclose()
