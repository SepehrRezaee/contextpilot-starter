# ContextPilot: decision log

Setup, run and test instructions are in [`assistant/README.md`](assistant/README.md). The root `README.md` is the supplied fixture's and is left unchanged.

## Questions I would ask, and the assumptions I used

1. **Who may approve a customer-facing action?** I assumed only the operator who proposed it, in the same tenant. Local JSON context stands in for authenticated claims.
2. **What counts as consent?** A separate approval request for the stored action ID and its exact body hash. Prose and retrieved text never authorize publication. If the ticket version changes or the draft is older than 24 hours, a new draft and a new review are required.
3. **What may memory retain, and for how long?** Only explicit, durable writing preferences (length, format, introductions), scoped by tenant and user, kept until they are corrected or forgotten. Forget is a real delete. Audit records (actions, redacted traces) are separate and would need their own retention policy.
4. **How is the refund window counted?** In calendar days from the first payment, with day 14 included for Northstar. Unknown prior credits or plan entitlement always require verification. The assistant never promises a refund or changes billing.
5. **What may recovery do after an ambiguous write?** Look up the original idempotency key first, then retry once with the same body and key. If the result is still unclear, the action stays `UNKNOWN`. Restart recovery is read-only and never republishes on its own.

## Architecture and trade-offs

- **One bounded orchestrator, not an agent loop.** Routing, tenant scope, tool choice, memory writes and approval are decided by code from the validated context. The model boundary (`ModelAdapter.render`) only formats verified evidence and has no tool or state capability. Less flexible than model-chosen tools, but injected text cannot cause a side effect.
- **Real MCP, persistent session.** The official SDK stdio client spawns the supplied server once and reuses the session; a broken session is reopened once, and every call has a timeout. MCP output is validated as data (schema plus tenant and ticket identity).
- **Retrieval proportional to nine documents.** Metadata filters (tenant or global, active, effective date) run first, then lexical topic ranking, then a bounded parser for refund terms. Conflicting or unparseable policy leads to escalation instead of a guess. Embeddings or a vector store would add nothing measurable at this corpus size.
- **SQLite for memory and the action ledger.** `synchronous=FULL` transactions; triggers make proposals immutable. Consent and the `SENDING` intent are durable before the POST. Stable keys plus reconciliation prevent duplicate publication. This is not formal exactly-once delivery.
- **Deterministic adapter.** The assignment allows it, and it makes the contract and safety tests reproducible. Operator answers cite document IDs inline. Customer drafts use plain wording, and their citations travel in the response.

**Deliberately omitted:** live LLM provider, UI, vector/graph store, OAuth, background reconciliation worker, multi-worker deployment.

## AI assistance and validation

AI coding assistants drafted most of the code, tests and documentation. I reviewed the design and checked behavior with evidence instead of trusting the generated code. Tests use the real fixture HTTP server, SDK stdio MCP server and subprocess restarts, and assert independent state such as backend comment counts, SQLite rows and trace events, not only the response text. Behavior fixes came with a regression test that was shown to fail before the fix. The evaluator regenerates `artifacts/`, so no result or trace in the repository is written by hand.

## First production improvement

Replace the trusted JSON context with authenticated tenant/user claims, and enforce ticket authorization in the backend and the MCP service over an authenticated transport. After that: a retention and encryption policy for memory and audit data, and a lease-based worker that reconciles `UNKNOWN` actions.
