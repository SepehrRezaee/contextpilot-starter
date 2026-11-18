# Approve and Publish (including Recovery)

## User Story

> As a support operator, I want to approve exactly the text I reviewed and have it posted once, even after 503s, stale versions or restarts.

## Entry Point

`POST /assistant/actions/{action_id}/approve` with the context and the `body_hash` of the stored draft. Recovery: `POST /assistant/actions/{action_id}/reconcile`.

## Steps

1. **Load and verify**
   - Owner-scoped load (tenant + user); body hash, binding hash and approval-record consistency re-verified.
   - Supplied `body_hash` compared in constant time → mismatch is 409 `body_hash_mismatch`.
   - `COMMITTED`/`SENDING` → returned unchanged (repeat approval is a no-op). `UNKNOWN` → resume (step 6).

2. **Read-only preflight**
   - Draft older than `proposal_ttl_hours` (24) → `CONFLICT/proposal_expired`.
   - `get_ticket` over MCP; version ≠ `expected_version` → `CONFLICT/stale_ticket_version`.
   - Nothing is recorded as consent if preflight fails.

3. **Record consent**
   - One `BEGIN IMMEDIATE` transaction: insert `approvals` row (server-generated `approval-<hex>`, session, hash) and set status `SENDING`, `attempts=1`.
   - A concurrent approval of the same draft loses the conditional update and returns the current state.

4. **Publish**
   - `CommentPublisher.publish` (only callable in `SENDING` with an approval reference) → `POST /v1/tenants/{t}/tickets/{id}/comments` with the **stored** body, version, key and reference.
   - 2xx → validate receipt (status, tenant, ticket, key, body, version = expected + 1) → `COMMITTED`.

5. **Ambiguous result (503 / timeout / 408 / 429 / 5xx)**
   - `GET /v1/tenants/{t}/actions/{idempotency_key}`:
     - found and matching → `COMMITTED` (no second POST)
     - 404 → retry with the **same** key and body (max `publish_attempts` = 2)
     - lookup unavailable or attempts exhausted → `UNKNOWN`

6. **Reconcile / resume**
   - `/reconcile`: read-only lookup by key; found → `COMMITTED`; not found → stays `UNKNOWN` (`not_found_no_automatic_republish`).
   - Approving an `UNKNOWN` action again: lookup first, then retry with the same key only if still absent.

## Async Side-Effects

None inside the assistant. The backend increments the ticket version and stores the action receipt on commit.

## Error States

| Situation | Status / code |
|---|---|
| 409 `version_conflict` from backend | `CONFLICT` |
| Other 409 (e.g. key reused for different body) or receipt mismatch | `INVARIANT_VIOLATION` — do not retry |
| Other 4xx | `FAILED/publish_rejected` |
| 503 after commit (`X-Demo-Failure: after`) | lookup → `COMMITTED`, attempts 1, one comment |
| 503 before commit (`X-Demo-Failure: before`) | lookup 404 → retry same key → `COMMITTED`, attempts 2 |
| Process dies after POST (status `SENDING`) | `/reconcile` after restart → `COMMITTED` |
| Wrong owner / tenant | 404 `action_not_found` |
| `CONFLICT`/`FAILED` approved again | 409 `action_not_approvable` (request a new draft) |
| Extra `approval_reference` field in request | 422 `invalid_request` |

Always read `action.status` even on HTTP 200 — `CONFLICT`, `UNKNOWN`, `FAILED` are explicit outcomes.

## Permissions

Only the proposing operator. Consent is bound to `(action_id, body_hash)`; prose approval is impossible.

## Related Code

- `assistant/actions.py` — `approve`, `_resume`, `_claim`, `_publish`, `_record_receipt`, `_resolve`, `reconcile`, `_reject_stale`
- `assistant/backend_client.py` — `CommentPublisher.publish`, `BackendReader.action`
- `assistant/api.py` — `approve`, `reconcile` endpoints
- Tests: `tests/test_actions.py` (503 before/after, concurrency, stale, 409 race, UNKNOWN, restart in SENDING, in-flight timeout), eval E06
