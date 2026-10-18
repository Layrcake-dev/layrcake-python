# OpenAPI spec issues

Defects found in https://api.layrcake.dev/openapi.json (API version 1.1.0) while building this SDK. Each one is for the gateway team to fix in `packages/contract/src/openapi.ts`. The SDK works around each one. The workarounds are in `RESPONSE_OVERRIDES`, `SUPPLEMENTAL_RESPONSES`, `SUPPLEMENTAL_QUERY` and `SKIPPED_BODY_FIELDS` in `scripts/generate.py`, and in `scripts/fallback_summaries.json`. Remove a workaround once the spec is fixed. The generator fails if the spec starts describing an envelope that an override still patches. The TypeScript SDK keeps the same list.

Checked against the live API on 2026-10-18 with free, read-only calls only.

## Response shapes

1. **`get_me` is wrapped live, flat in the spec.** The spec describes `{ key, workspace, limits, guard, credits, upkeep, billing }`. Live, `GET /v1/me` returns `{ "result": { key, workspace, guard, credits, upkeep, billing } }`. The live body also had no `limits` for the QA key. The spec lists it as optional, so that is allowed.
2. **`get_operation` has no 200 schema.** Live, the body is `{ "result": { operation_id, kind, state, progress, result, error, created_at, completed_at, items?, page? } }`. `state` values come from the `operation_state` enum: queued, running, waiting_external, succeeded, partially_succeeded, failed, cancelled, unknown. The SDK's `GetOperationResult` type comes from `SUPPLEMENTAL_RESPONSES` in `scripts/generate.py` and should be replaced by a spec schema.
3. **`get_operation` leaves out the `cursor` query parameter.** Live main pages finished list-mode operations with `?cursor=` from `page.next_cursor`.
4. **`pause_campaign` has no 200 schema.** Live code returns `{ "result": { ..., replayed } }`.
5. **`launch_campaign` and `reply_to_thread` answer 202 with a body that is not wrapped.** The 202 body is `{ status, approval }` or `{ request_id, status }`, while every 200 uses `{ result }`. Either wrap it or document it as intended.
6. **`setup_sending`, `launch_campaign` and `reply_to_thread` 200 responses are an `anyOf` of `{ result }` and `{ result, layrcake }`.** It is not clear when the billing block is missing. Document when, or always include it.
7. **`get_replies`, `get_reply`, `get_trail`, `kill_sending`, `import_do_not_contact` and `tighten_key_policy` return `{ result }` with no `layrcake` block.** That is fine, but the docs say every success is `{ result, layrcake }`. Make one of the two true.
8. **`get_campaign_proof` is described as a flat body.** It could not be checked for free under the probe rules. Given items 1 and 2, it is probably wrapped too. The SDK unwraps any `{ result }` or `{ result, layrcake }` body whatever the spec says, so either shape works.

## Approvals

9. **`launch_campaign` result schema leaves out `approval_url`.** The route sends it with the preview (`sending.ts`, the `created.approvalUrl` field). `add_leads`, `setup_sending` and `reply_to_thread` include it.
10. **`setup_sending` request body lists `approval_url`.** It is a response field and should not be an input.

## Idempotency and retries

11. **The MCP tools describe the key as `idempotency_key`, but REST uses an `Idempotency-Key` header.** That is fine, but say so in the REST docs, since agents read both.
12. **No `Retry-After` header.** Retry hints are only in `error.details.retry_after_seconds`. Add the standard header on 429 and 503 so generic HTTP clients back off correctly.
13. **No request-id header on most responses.** `X-Layrcake-Request-Id` is set on `/v1/llm/chat` only. Bare `{ result }` responses and bodiless errors have no request id at all. Add the header to every response.

## Errors

14. **Several error responses have no body schema:** `export_trail_csv` and `get_campaign_proof` (400, 401, 403, 404), `import_do_not_contact` (400, 403, 404, 409, 429) and `pause_campaign` (404). If they send the standard `{ error }` envelope, reference it. If they send no body, the SDK falls back to a code from the status.
15. **`get_me` and `get_operation` declare no error responses at all.**
16. **HTTP status by code is not in the spec.** For example, `confirmation_required` is 403, `run_budget_exceeded` is 403 and the `approval_*` codes are 409. An `x-status` per code, or a table in the description, would let clients map errors without reading the gateway source.

## Documentation

17. **20 of 28 operations have no `summary` or `description`.** These are all 19 POST verbs (`verify_email` through `llm`) plus `get_operation`. SDK method docs, the README table and generated docs all need one line each.
18. **Only `find_leads` and `research_company` have a request `example`.**
19. **`llm` documents `stream: true` but not the streamed response.** There is no `text/event-stream` content type, and nothing on `stream_consumed`. The SDK does not expose `stream` for now.

## find_leads / find_companies required filters
The live API refuses find_leads without at least one of titles, industries, locations or company_domains (and requires audience), and find_companies without one of query, hiring, technologies or similar_to. The spec marks these optional, so typed requests can be refused at runtime. Express it in the schema (e.g. a refinement documented in the description, or oneOf groups).
