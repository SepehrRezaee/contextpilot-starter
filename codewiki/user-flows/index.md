# User Flows

All flows start from the assistant API (no UI). Every request carries trusted `tenant_id`, `user_id` and `session_id`.

| # | Flow | Entry point | Side effect on customer? | Eval scenarios |
|---|---|---|---|---|
| 1 | [Ticket investigation](ticket-investigation.md) | `POST /assistant/respond` "Explain NS-104…" | No | E01, E08 |
| 2 | [Policy question](policy-question.md) | `POST /assistant/respond` "What is the current refund policy?" | No | — |
| 3 | [Writing preferences](writing-preferences.md) | `POST /assistant/respond` "I prefer…", memory endpoints | No | E02, E03, E04 |
| 4 | [Draft a customer reply](draft-reply.md) | `POST /assistant/respond` "Draft a response for NS-104" | No (stores a proposal) | E05 |
| 5 | [Approve and publish](approve-and-publish.md) | `POST /assistant/actions/{id}/approve`, `/reconcile` | **Yes — the only one** | E06 |
| 6 | [Scope and safety declines](scope-and-safety.md) | `POST /assistant/respond` with foreign/unsafe prose | No | E07, E08 |

## User stories

- As a support operator, I want to ask about a ticket and get the current policy with citations, so that I can answer without promising an unverified refund.
- As a support operator, I want to ask about a policy topic without a ticket, so that I can check the current rules quickly.
- As a support operator, I want my explicit writing style remembered across sessions, and to correct or delete it, so that I don't repeat myself and stale preferences never return.
- As a support operator, I want a draft I can inspect before anything reaches the customer, so that a draft is never mistaken for a sent message.
- As a support operator, I want to approve exactly the text I reviewed and have it posted once, even after 503s, stale versions or restarts.
- As a tenant, I want other tenants' data, prose "send" commands and injected document text to be unable to trigger any tool, so that my customers are protected.

## Roles

There is one role: the operator, identified by `(tenant_id, user_id)`. Ownership checks (actions, memory) use both; there are no admin or cross-tenant roles.
