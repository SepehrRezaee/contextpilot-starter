# Ticket Investigation

## User Story

> As a support operator, I want to ask about a ticket and get the current policy with citations, so that I can answer without promising an unverified refund.

## Entry Point

`POST /assistant/respond` with a message naming one ticket in the operator's tenant, e.g. `Explain NS-104 and cite the current refund policy`.

## Steps

1. **Validate and route**
   - API call: `POST /assistant/respond`
   - Pydantic `RespondRequest` validates context (tenant ∈ `northstar|harbor`, safe identifiers, message 1–4000 chars, no extra fields).
   - `Orchestrator.respond` extracts ticket IDs, rejects foreign tenants/tickets, finds no forget/preference/recall/publish intent → intent `investigate`.

2. **Read the ticket over MCP**
   - `TicketMCP.get_ticket(context, ticket_id)` → `tools/call get_ticket` with `tenant_id` **from context**.
   - Result validated as `Ticket`; tenant and id must equal the request. Up to 2 attempts; a broken reused session reconnects once.

3. **Retrieve documents**
   - `BackendReader.documents(tenant)` → `GET /v1/tenants/{tenant}/documents` (2 attempts on transport/5xx errors).
   - `select_documents(documents, tenant, message + ticket.subject, today)`: tenant/global filter → active + `effective_at ≤ today` → topic by title → tenant beats global → conflict checks → highest-ranked per topic.

4. **Assess evidence**
   - `assess(ticket, retrieval, today)`: parse the refund rule (`refund_terms`), compute calendar days since `purchase_date`, list missing facts (e.g. `prior_credits`), choose `verification_required` / `manual_escalation` / `insufficient_evidence`.

5. **Render**
   - Stored preferences shape the text (`length`, `format`, `introductions`); `DeterministicAdapter.render` emits fixed-template claims with `[DOCUMENT-ID]`.
   - Data changed: none. `proposed_action` is `null`.

6. **Trace**
   - One JSONL record: tools `mcp:get_ticket`, `http:get_documents`; `selected_document_ids`; stage timings.

Example (NS-104, clock 2026-11-16): citations `N-POL-REFUND-2026`, `N-POL-BILL-2026`; 13 of 14 days; `verification_required`; missing `prior_credits`. On 2026-11-18 the same ticket is 15 days old → `manual_escalation`.

## Error States

| Situation | Result |
|---|---|
| Unknown ticket (e.g. `NS-999`) | 404 `ticket_not_found` |
| MCP script missing, timeout, dead process | 502 `mcp_unavailable` (no HTTP fallback) |
| MCP returns another tenant / malformed JSON | 502 `malformed_mcp_result` |
| Backend documents unavailable | 502 `backend_unavailable` |
| Superseded, future-dated or conflicting policies | excluded or `insufficient_evidence` with `issues` |
| Unsupported or negated policy wording | `insufficient_evidence`, no citation |
| More than one ticket in the message | `clarification` |

## Permissions

Operator in the ticket's tenant. The ticket prefix must belong to that tenant (`NS-` → northstar, `HB-` → harbor).

## Related Code

- `assistant/orchestrator.py` — `Orchestrator.respond`
- `assistant/mcp_client.py` — `TicketMCP.get_ticket`, `normalize_ticket`
- `assistant/backend_client.py` — `BackendReader.documents`
- `assistant/retrieval.py` — `select_documents`, `refund_terms`, `assess`
- `assistant/model_adapter.py` — `DeterministicAdapter.render`
- Tests: `tests/test_security.py::test_api_investigation_is_grounded_and_encounters_injection_as_data`, `tests/test_retrieval.py`, `tests/test_mcp_integration.py`
