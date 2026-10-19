# Changelog

All notable changes to this package are listed here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the package uses [Semantic Versioning](https://semver.org/).

## 0.1.0 (2026-10-18)

First release.

- A typed method for each of the 28 operations in the Layrcake OpenAPI spec (API version 1.1.0), on a sync client (`Layrcake`) and an asyncio client (`AsyncLayrcake`).
- Request and response types generated from the spec as `TypedDict`s. Paid calls return a typed plan when called with `dry_run=True`.
- Every method returns a `LayrcakeResponse` with `result`, `layrcake` and `request_id`. Credit amounts are `Decimal`, never floats.
- Bodies shaped `{result}` or `{result, layrcake}` are unwrapped by shape, so `get_me` and `get_operation` work as the live API sends them. `llm` and the CSV export stay as sent.
- Automatic Idempotency-Key on every endpoint that takes one, reused across retries.
- Retries on 429, 502, 503, 504 and network errors with backoff, jitter and Retry-After.
- Typed errors: `LayrcakeError` and subclasses for auth, permission, approval, credits, bad request, not found, conflict, rate limit, server, connection and timeout.
- `wait_for_operation` for background work.
- `approval_required` for actions that need a person to approve. The SDK never approves anything.
- One runtime dependency: httpx. Python 3.9 or later. Ships `py.typed`.
