# Policy Question

## User Story

> As a support operator, I want to ask about a policy topic without a ticket, so that I can check the current rules quickly.

## Entry Point

`POST /assistant/respond` with a message containing a policy word (`policy`, `policies`, `refund`, `billing`, `cancellation`) and no ticket ID, e.g. `What is the current refund policy?`.

## Steps

1. **Route** — no ticket, not a draft, policy word present → intent `policy`.
2. **Retrieve** — `GET /v1/tenants/{tenant}/documents`; `select_documents` with the message as the query (same filters as an investigation).
3. **Assess** — `assess(None, retrieval, today)`: claims only; assessment `policy_only` when claims exist and there are no issues, otherwise `insufficient_evidence`.
4. **Render** — claims with citations plus "Provide the ticket ID to check these conditions against its verified records."
   - Data changed: none; no MCP call.

## Error States

- No policy word and no ticket → `clarification` ("Please specify a ticket ID…").
- Policy word plus another tenant's name (e.g. "Harbor refund policy" in northstar) → `scope_decline` before any HTTP call.
- Backend unavailable → 502 `backend_unavailable`.

## Permissions

Operator in the current tenant; only that tenant's and `global` documents are considered.

## Related Code

- `assistant/orchestrator.py` — `policy_query` branch of `Orchestrator.respond`
- `assistant/retrieval.py` — `select_documents`, `assess`
