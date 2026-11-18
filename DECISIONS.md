# ContextPilot decisions

## Questions and working assumptions

1. **Who may approve?** Only the proposing operator in the same tenant. Local JSON identity is trusted demo context; production must derive it from authenticated claims and enforce ticket entitlements.
2. **What constitutes consent?** A separate approval request for the stored action ID and preview body hash. Prose, retrieved instructions, and a draft never authorize publication. Changed ticket versions require a new proposal and review.
3. **What is retained?** Only explicit, bounded writing preferences, scoped by tenant/user, until corrected or forgotten. Forget deletes current preferences, without a cache or chat-summary fallback. Action audit records and redacted traces are separate; production needs a retention policy for these records.
4. **What is a refund window?** Elapsed calendar dates, inclusive of day 14 for Northstar; the evaluation clock is 2026-11-16. Unknown prior credits or plan entitlement require verification. Never promise a refund or change billing state.
5. **What may recovery do?** Retry at most twice with the original immutable payload/key, after checking backend action status. Uncertain reconciliation remains UNKNOWN. Restart recovery is read-only and never automatically republishes.

## Choices and omissions

One bounded orchestrator separates trusted routing, MCP reads, policy selection, preference updates and approval-controlled HTTP writes. The deterministic adapter renders calculated evidence; it cannot choose tools, identity or approvals. It demonstrates the contract without measuring live-model quality. Lexical/title metadata and bounded policy parsing fit nine documents; unsupported or contradictory policy language causes escalation. SQLite transactions and immutable proposal fields keep consent and intended writes durable. Stable keys plus backend deduplication and reconciliation prevent repeat publication of the same action; this is not formal exactly-once delivery.

The original seven starter files remain unchanged, including its README; candidate instructions are in `assistant/README.md`. The PDF asks for at least three automated checks; the goal strengthens this to all eight scenarios plus failure cases. Python 3.11 is available locally and satisfies the starter; 3.12 is a recommendation, not a requirement. The folder has no Git metadata, so work remains in the explicitly authorized workspace, without creating a repository or worktree.

No live provider, UI, vector database, OAuth or cloud deployment. AI coding assistance produced and reviewed code, tests and documentation; real fixture HTTP, SDK stdio MCP, subprocess restarts and independent state assertions verify behavior. Test results and traces are generated, not hand-scored.

**First production improvement:** authenticated tenant/user claims plus service-side ticket authorization, including an authenticated MCP/backend transport. Then add retention/deletion policy, encryption and a lease-based recovery worker before serving real customer data.

