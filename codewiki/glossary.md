# Glossary

| Term | Meaning in ContextPilot |
|---|---|
| **Operator** | Internal support user identified by `(tenant_id, user_id)` who asks questions and approves actions |
| **Tenant** | Customer organisation whose data is isolated: `northstar` (`NS-` tickets) or `harbor` (`HB-` tickets) |
| **Trusted context** | `tenant_id`, `user_id`, `session_id` supplied by the application layer, never inferred from message text, documents or model output |
| **Fixture** | The supplied, unchanged starter infrastructure: `server.py`, `mcp_server.py`, `fixtures.json` and related files |
| **Intent** | The deterministic route chosen for a message (`investigate`, `draft`, `policy`, `preference_update`, `memory_recall`, `forget`, `approval_required`, `scope_decline`, `clarification`) |
| **Scope decline** | Refusal before any tool access when prose names another tenant or a foreign ticket |
| **Grounding** | Basing every policy claim on a selected current document and citing its ID |
| **Citation** | Document ID of a source actually used for a claim (e.g. `N-POL-REFUND-2026`) |
| **Encountered vs selected** | Documents seen during retrieval (recorded in the trace) vs documents actually used for claims |
| **Superseded** | A policy whose `status` is not `active` (e.g. `N-POL-REFUND-2024`); never cited |
| **Topic** | Policy category derived from a document title: refund, billing, cancellation, escalation |
| **Assessment** | Outcome of evidence evaluation: `verification_required`, `manual_escalation`, `insufficient_evidence`, `policy_only` |
| **Refund window** | Calendar days from first/original payment allowed by the current refund policy (14 for northstar, 7 for harbor) |
| **Premium exports** | Usage signal that disqualifies a northstar refund when greater than zero |
| **Prior credits** | Required billing fact absent from all fixture tickets, so refunds are never confirmed |
| **Writing preference** | Explicit, durable style setting (`length`, `format`, `introductions`) stored per tenant and user |
| **Provenance** | `source_session` and timestamps recorded with each preference |
| **Proposal / draft** | Immutable stored action in `PROPOSED` state; not a sent message |
| **Body hash** | SHA-256 of the exact draft body; approval must present it |
| **Binding hash** | SHA-256 over all immutable proposal fields, re-verified on every load |
| **Approval reference** | Server-generated `approval-<hex>` recorded with consent and sent to the backend |
| **Idempotency key** | Stable `cp-<hex>` created at proposal; every publish attempt and lookup uses it |
| **Preflight** | Read-only check before consent: draft TTL and current ticket version via MCP |
| **Reconcile** | Read-only lookup of an action's outcome by idempotency key; never republishes |
| **UNKNOWN** | Honest state when a write's outcome cannot be confirmed |
| **INVARIANT_VIOLATION** | Backend record disagrees with the stored proposal; never retried |
| **Deterministic adapter** | Offline renderer standing in for an LLM; turns evidence into text with no tool or approval authority |
| **Trace** | Redacted JSONL record of one request: IDs, tools, documents, action state, errors, stage timings |
| **E01–E08** | The eight supplied evaluation scenarios in `eval_scenarios.json` |
| **Hostile document** | `N-OPS-NOTE-2026`, an active note containing a prompt-injection instruction; treated purely as data |
