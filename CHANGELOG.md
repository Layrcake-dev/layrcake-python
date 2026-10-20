# Changelog

All notable changes to this package are listed here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the package uses [Semantic Versioning](https://semver.org/).

## 0.2.0 (unreleased)

### Agent tools

- New `layrcake.agent_tools` module: eight framework-neutral tools for AI agents (`find_leads`, `enrich`, `verify_email`, `check_companies`, `research_company`, `create_campaign`, `launch_campaign`, `get_me`) with pydantic argument models that match `layrcake.types`, and descriptions that state each price.
- `LayrcakeSession` (sync) and `AsyncLayrcakeSession` (asyncio) hold one run's rules: the run budget passed to every paid call as `budget_credits`, `dry_run` on every call, idempotency keys of `run_id`, verb and a hash of the arguments, campaigns limited to emails verified in the run, and a launch that stops at a person's approval and never takes a confirmation token. Errors come back as `Refused:` text.
- Each verb is written once and runs on both clients, so the sync and async tools behave the same. Idempotency keys match those of langchain-layrcake, llama-index-tools-layrcake and crewai-layrcake 0.1.0.
- New optional extra `agents` (pydantic 2). The core client still depends only on httpx, and nothing in the existing public API changed.

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
