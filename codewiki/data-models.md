# Data Models

## Request models (`assistant/schemas.py`, Pydantic)

| Model | Fields | Notes |
|---|---|---|
| `Context` | `tenant_id`, `user_id`, `session_id` | `extra="forbid"`, `frozen=True`; trusted local claims |
| `RespondRequest` | `Context` + `message` | 1–4000 chars, whitespace-stripped |
| `ApproveRequest` | `Context` + `body_hash` | `^[a-f0-9]{64}$` |

Tenant registry: `TENANTS = {"northstar": "NS", "harbor": "HB"}` — the ticket prefix for each tenant. Adding a tenant means updating `TENANTS` and the `Tenant` literal.

## Domain models

| Model | Fields | Source |
|---|---|---|
| `Ticket` | `tenant_id`, `id` (`^[A-Z]{2,5}-[1-9]\d{0,6}$`), `version ≥ 1` (strict int), `status`, `subject`, `body`, `purchase_date?`, `premium_exports_used? ≥ 0`, `prior_credits? ≥ 0`, `plan?`, `comments[]` | MCP `get_ticket`; extra fields ignored |
| `Document` | `id`, `tenant_id`, `title`, `text`, `effective_at` (date), `status` | backend documents; invalid ones are excluded with an issue |
| `Retrieval` | `documents[]`, `encountered_ids[]`, `issues[]`, `ranking[]` | `retrieval.select_documents` |
| `Evidence` | `ticket_id`, `ticket_version`, `as_of`, `purchase_date`, `exports_used`, `window_days`, `elapsed_days`, `assessment`, `claims[]`, `missing[]`, `issues[]`; property `citations` | `retrieval.assess` |

`assessment`: `verification_required` (inside window, still unverified), `manual_escalation` (outside window, exports used, prior credits, enterprise plan), `insufficient_evidence` (no/unsupported/conflicting policy, missing purchase date, future date), `policy_only` (no ticket).

## SQLite (`var/assistant.sqlite3`)

One connection, `journal_mode=WAL`, `synchronous=FULL`, `foreign_keys=ON`, `busy_timeout=5000`.

### `preferences` (`assistant/storage.py`)

| Column | Notes |
|---|---|
| `tenant_id`, `user_id`, `key` | primary key |
| `value` | `length`: concise\|detailed · `format`: bullets\|paragraphs · `introductions`: omit\|include |
| `source_session` | session of the latest write (provenance) |
| `created_at`, `updated_at` | UTC ISO timestamps |
| `source` | default `explicit_user_instruction` |

### `actions` (`assistant/actions.py`)

| Column | Notes |
|---|---|
| `action_id` | PK, UUID4 |
| `tenant_id`, `user_id`, `ticket_id` | owner and target |
| `body` | 1–2000 chars (CHECK) |
| `body_hash` | SHA-256 of `body` |
| `expected_version` | ticket version at proposal (> 0) |
| `idempotency_key` | `cp-<hex>`, UNIQUE |
| `created_at` | proposal time (TTL anchor) |
| `binding_hash` | SHA-256 over all immutable fields |
| `status` | see below |
| `approval_reference`, `approved_at` | set once at approval |
| `attempts`, `error_code`, `receipt_json` | publish bookkeeping |

### `approvals`

| Column | Notes |
|---|---|
| `action_id` | PK, FK → `actions` |
| `tenant_id`, `user_id`, `session_id` | approving operator |
| `body_hash` | hash that was approved |
| `approval_reference` | `approval-<hex>`, UNIQUE, server-generated |
| `approved_at` | UTC timestamp |

### Triggers

- `proposal_immutable` — aborts updates to any immutable proposal column or `binding_hash`.
- `approval_immutable` — aborts any update of an approval row.
- `approval_reference_immutable` — the reference cannot change once set.

`Actions._load` re-verifies body hash, binding hash and the actions/approvals consistency on every read.

### Action statuses

| Status | Meaning | Next step |
|---|---|---|
| `PROPOSED` | stored draft, nothing sent | approve with `body_hash` |
| `SENDING` | consent recorded, publish attempted | reconcile |
| `COMMITTED` | backend receipt verified | none |
| `UNKNOWN` | outcome unconfirmed | reconcile, or approve again (same key) |
| `CONFLICT` | ticket changed or draft expired | new draft |
| `FAILED` | backend rejected | new draft |
| `INVARIANT_VIOLATION` | backend record ≠ proposal | investigate via trace; never retry |
| `APPROVED` | legacy (0.1 databases) | approve again |

## Trace record (`var/traces.jsonl`)

`trace_id`, `tenant_id`, `user_id`, `session_id`, `started_at`, `intent`, `model_adapter`, `outcome`, `selected_document_ids`, `tools`, `action{action_id,status}`, `error`, `duration_ms`, `events[{stage, elapsed_ms, ...}]`. No prompts, ticket bodies, answers or secrets.

## Fixture data (`fixtures.json`)

| Document | Tenant | Status | Note |
|---|---|---|---|
| `GLOBAL-001` | global | active | publishing needs verification and approval |
| `N-POL-REFUND-2024` | northstar | superseded | 30 days — must not be cited |
| `N-POL-REFUND-2026` | northstar | active | 14 calendar days, no premium exports |
| `N-POL-BILL-2026` | northstar | active | check purchase age, exports, prior credits |
| `N-POL-CANCEL-2026` | northstar | active | cancellation ≠ refund |
| `N-OPS-NOTE-2026` | northstar | active | **hostile prompt-injection example** |
| `H-POL-REFUND-2026` | harbor | active | 7 days, enterprise excluded |
| `H-POL-ESC-2026` | harbor | active | escalation rules |
| `H-OPS-EXTRA` | harbor | active | tone guidance |

| Ticket | Tenant | Version | Purchase | Exports |
|---|---|---|---|---|
| `NS-104` | northstar | 1 | 2026-11-03 | 0 |
| `NS-105` | northstar | 2 | 2026-08-11 | 7 |
| `HB-204` | harbor | 1 | 2026-11-08 | 0 |

No ticket has `prior_credits`, so a refund is never confirmed.
