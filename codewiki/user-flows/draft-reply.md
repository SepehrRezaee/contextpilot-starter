# Draft a Customer Reply

## User Story

> As a support operator, I want a draft I can inspect before anything reaches the customer, so that a draft is never mistaken for a sent message.

## Entry Point

`POST /assistant/respond` with `draft` or `propose` and one ticket ID, e.g. `Draft a response for NS-104`.

## Steps

1. **Investigate** — identical to [ticket investigation](ticket-investigation.md) steps 1–5, with `draft=True` (a greeting is added unless the style is concise or introductions are omitted).
2. **Propose**
   - `Actions.propose(context, ticket, body)` validates scope and body (1–2000 chars).
   - Stores an immutable `actions` row: `action_id` (UUID4), owner, ticket, exact `body`, `body_hash` (SHA-256), `expected_version` (ticket version read via MCP), `idempotency_key` (`cp-<hex>`), `binding_hash`, status `PROPOSED`.
   - Data changed: one `actions` row. **No** approval row, **no** backend comment.
3. **Return** — `proposed_action` includes the full body, hash, key, `requires_approval: true` and `next_step`.
4. **Inspect** — `GET /assistant/actions/{action_id}?tenant_id&user_id&session_id` returns the stored proposal (re-verified for integrity on every load).

## Error States

- No ticket ID → `clarification`.
- Ticket missing or MCP down → 404/502; no proposal is stored.
- Rendered body over 2000 chars → 422 `rendered_body_too_long`.
- Message also says "send/approve" → still only a draft; prose never approves.
- Foreign owner inspecting → 404 `action_not_found`.

## Permissions

Only the proposing operator (same tenant and user) can inspect, approve or reconcile the action.

## Related Code

- `assistant/orchestrator.py` — draft branch → `self.actions.propose`
- `assistant/actions.py` — `Actions.propose`, `_load`, `public`, `inspect`, SQLite triggers
- Tests: `tests/test_actions.py::test_draft_is_immutable_persistent_and_never_writes`, eval E05
