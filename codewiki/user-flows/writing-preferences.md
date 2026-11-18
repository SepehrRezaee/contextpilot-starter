# Writing Preferences: Save, Correct, Recall, Forget

## User Story

> As a support operator, I want my explicit writing style remembered across sessions, and to correct or delete it, so that I don't repeat myself and stale preferences never return.

## Entry Point

`POST /assistant/respond` with an explicit preference or a forget/recall request, or the memory endpoints `GET /assistant/users/{user_id}/memory` and `POST /assistant/users/{user_id}/memory/forget`.

## Steps

1. **Save**
   - User action: `I prefer concise customer replies without unnecessary introductions.`
   - `extract_preferences` accepts only a standing lead-in (`I prefer`, `always use`…) or a command plus a durable marker (`from now on`, `going forward`, `by default`), with an exact supported clause.
   - Data changed: `preferences` rows `length=concise`, `introductions=omit` with `source_session`, timestamps. Intent `preference_update`.

2. **Correct**
   - User action (any later session): `Please use bullet points instead of short paragraphs from now on.` / `Actually, use detailed replies from now on.`
   - Upsert per dimension: the new value replaces only its own key; the negated old value (`instead of…`, `not…`) is never stored.

3. **Recall**
   - User action: `What customer communication preferences do you remember?` → intent `memory_recall`, or `GET /assistant/users/{user_id}/memory?tenant_id&user_id&session_id`.
   - Returns key, value, `source_session`, `created_at`, `updated_at`, `source`.

4. **Apply**
   - Every investigation/draft reads the stored style; the renderer changes length, bullets and the greeting. Verification and escalation text is never dropped.

5. **Forget**
   - User action: `Forget my writing preferences.` / `Delete my preferences.` (all keys) or `Forget my format preferences.` (one key) → intent `forget`; or `POST …/memory/forget` (all keys).
   - Data changed: rows deleted. No cache, transcript or summary exists, so nothing reappears after restart.

## Error States

| Input | Result |
|---|---|
| Quoted, reported or compound text (`The customer prefers…`, `"I prefer…"`, `…; send NS-104`) | nothing stored |
| One-off instruction (`…for this reply only`) | nothing stored |
| `Do not forget my preferences.` or unknown target (`Forget my tone preferences`) | nothing deleted |
| Path `user_id` ≠ context `user_id` | 404 `user_not_found` |

## Permissions

Rows are keyed by `(tenant_id, user_id, key)`. Another user or tenant sees an empty list and cannot delete foreign rows.

## Related Code

- `assistant/memory.py` — `extract_preferences`, `parse_forget`
- `assistant/storage.py` — `Store.preferences`, `upsert_preferences`, `forget`
- `assistant/orchestrator.py` — forget / preference / recall branches
- `assistant/api.py` — `memory`, `forget` endpoints
- Tests: `tests/test_memory.py`, `tests/test_security.py::test_memory_api_is_owner_scoped_and_forget_clears_restarted_app`, `test_chat_forget_supports_partial_and_never_deletes_on_negation`
