# API Reference

## Assistant API (port 8000, `assistant/api.py`)

Interactive schema: `http://127.0.0.1:8000/docs`. All context fields are required and validated; unknown fields are rejected.

**Context fields**

| Field | Type / rule |
|---|---|
| `tenant_id` | `northstar` \| `harbor` |
| `user_id` | `^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$` |
| `session_id` | same pattern as `user_id` |

**Error envelope:** `{"error": "<code>", "trace_id": "<uuid>"}`. Any validation failure → 422 `invalid_request` (the body is not echoed).

### Health

| Method | Path | Response |
|---|---|---|
| GET | `/health` | `{"status":"ok","service":"contextpilot-assistant","model_adapter":"deterministic"}` — process readiness only |

### Conversation

| Method | Path | Body | Response |
|---|---|---|---|
| POST | `/assistant/respond` | context + `message` (1–4000 chars, trimmed) | `answer`, `citations[]`, `proposed_action` (or null), `trace_id`, `intent`, `preferences[]`, `evidence` (or null) |

`intent` values: `scope_decline`, `forget`, `preference_update`, `memory_recall`, `approval_required`, `clarification`, `investigate`, `draft`, `policy`.

`evidence` (investigate/draft/policy): `ticket_id`, `ticket_version`, `as_of`, `purchase_date`, `premium_exports_used`, `window_days`, `elapsed_days`, `assessment`, `missing[]`, `issues[]`, `claims[{document_id,text}]`.

### Actions

| Method | Path | Body / query | Response |
|---|---|---|---|
| GET | `/assistant/actions/{action_id}` | query: context | `{"action": {...}}` |
| POST | `/assistant/actions/{action_id}/approve` | context + `body_hash` (64 lowercase hex) | `{"action": {...}, "trace_id"}` |
| POST | `/assistant/actions/{action_id}/reconcile` | context | `{"action": {...}, "trace_id"}` (read-only) |

`action_id` must be a UUID. Action object: `action_id`, `tenant_id`, `user_id`, `ticket_id`, `body`, `body_hash`, `expected_version`, `idempotency_key`, `created_at`, `status`, `approved_at`, `attempts`, `error_code`, `receipt`, `requires_approval`, `next_step`. See [data models](data-models.md#action-statuses).

### Memory

| Method | Path | Body / query | Response |
|---|---|---|---|
| GET | `/assistant/users/{user_id}/memory` | query: context | `{"preferences": [{key,value,source_session,created_at,updated_at,source}]}` |
| POST | `/assistant/users/{user_id}/memory/forget` | context | `{"status":"forgotten","deleted":n}` |

Path `user_id` must equal the context `user_id`, otherwise 404 `user_not_found`.

### Error codes

| Code | HTTP | Raised in |
|---|---|---|
| `invalid_request` | 422 | `api.validation_error` |
| `invalid_ticket_id` | 422 | `schemas.validate_ticket_scope` |
| `ticket_scope` | 403 | `schemas.validate_ticket_scope`, `Actions.propose` |
| `tenant_scope` | 403 | `backend_client.tenant_path` |
| `ticket_not_found` | 404 | `mcp_client.normalize_ticket` |
| `mcp_unavailable` | 502 | `TicketMCP.get_ticket` |
| `mcp_tool_error`, `malformed_mcp_result` | 502 | `mcp_client.normalize_ticket` |
| `backend_unavailable`, `backend_read_rejected`, `malformed_documents` | 502 | `BackendReader` |
| `action_not_found` | 404 | `Actions._load` (also for foreign owners) |
| `body_hash_mismatch` | 409 | `Actions.approve` |
| `action_not_approvable` | 409 | `Actions.approve` |
| `proposal_integrity_violation`, `approval_integrity_violation` | 409 | `Actions._load` |
| `approval_required` | 409 | `CommentPublisher.publish` (defensive guard) |
| `invalid_comment_body`, `rendered_body_too_long` | 422 | `Actions.propose`, `DeterministicAdapter.render` |
| `user_not_found` | 404 | `api.check_user` |
| `internal_error` | 500 | `api.execute` fallback |

Action `error_code` values: `stale_ticket_version`, `proposal_expired`, `version_conflict`, `publish_rejected`, `receipt_mismatch`, `publish_unconfirmed_after_retries`, `reconciliation_unavailable`, `not_found_no_automatic_republish`, `invalid_conflict_response`, `idempotency_key_reused_for_different_action`.

## Fixture backend (port 8765, `server.py`)

| Method | Path | Notes |
|---|---|---|
| GET | `/health` | `{"status":"ok","service":"contextpilot-mock"}` |
| GET | `/v1/fixtures/sessions` | sample sessions from `fixtures.json` |
| GET | `/v1/tenants/{tenant}/documents` | tenant + `global` documents |
| GET | `/v1/tenants/{tenant}/tickets/{ticket}` | ticket with current `version` |
| POST | `/v1/tenants/{tenant}/tickets/{ticket}/comments` | `{body≤2000, expected_version:int, idempotency_key≤128, approval_reference}` → 201 event |
| GET | `/v1/tenants/{tenant}/actions/{idempotency_key}` | committed event or 404 `action_not_found` |
| POST | `/__admin/reset` | `{}` — restore fixture state |

Failure switches: `?delay_ms=N` on GET (max 5000); header `X-Demo-Failure: before` (503, nothing saved) or `after` (503 after commit); wrong `expected_version` → 409 `version_conflict`; same key, different action → 409 `idempotency_key_reused_for_different_action`. Key dedup is checked **before** the version, so a retry of a committed action returns 200 with the original event. The backend only checks that `approval_reference` is present — consent is enforced by the assistant.

## MCP tool (`mcp_server.py`)

| Tool | Arguments | Returns |
|---|---|---|
| `get_ticket` | `tenant_id: str`, `ticket_id: str` | ticket JSON (read-only; HTTP 404 surfaces as a tool error) |

Called only by `TicketMCP` over stdio, with `tenant_id` taken from trusted context.
