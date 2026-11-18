# ContextPilot implementation plan

> **For agentic workers:** Use superpowers:executing-plans inline. The user explicitly requests immediate autonomous execution without approval pauses.

**Goal:** Implement the complete local candidate solution, without changing any supplied fixture file.

**Architecture:** FastAPI validates trusted local context. One bounded orchestrator uses read-only stdio MCP for tickets, scoped HTTP document retrieval, a deterministic evidence renderer, and SQLite preferences/actions. Only a separately recorded owner approval can reach the HTTP publisher.

**Tech stack:** Python 3.11+, FastAPI/Pydantic, HTTPX, official MCP 1.26.0, SQLite, pytest.

**Spec:** User objective attachment and `D:/Mohaymen/Mohaymen_ContextPilot_Candidate_Assignment.pdf`; exact fixture routes in the seven unchanged root files. `DECISIONS.md` records unresolved product assumptions.

## Global constraints

- No fixture imports in candidate code or tests; integrate over actual HTTP and MCP.
- Identity comes from validated application context; do not route from model/document content.
- Body limit 2,000 characters; stable idempotency key; separately stored approval; no silent rebase.
- Current active, nonfuture, scoped sources only; no global override of tenant policy.
- All E01–E08 measured offline; original smoke suite preserved; no paid credentials.
- No Git repository exists; keep original root README and place candidate README under assistant/.

## Review focus

Malformed MCP results and missing/startup-failing tools must fail closed (Task 1). Conflicting/current/future policy and calendar boundaries must prevent unsupported claims (Task 2). Quoted or incidental preference prose must never persist (Task 3). Simultaneous approvals and restart during uncertain publish must not duplicate (Task 4). Diagnostic endpoints must enforce same-user ownership as writes (Tasks 4–5).

## Tasks

- [x] **1. Core integration:** Write `tests/test_mcp_integration.py` and subprocess HTTP fixture helper first; observe missing implementation failure. Implement `schemas.py`, `config.py`, `errors.py`, `tracing.py`, `backend_client.py`, `mcp_client.py`. Interface: `get_ticket(context, ticket_id, trace) -> Ticket`; normalize SDK structured/text results and validate identity/version. Verify real MCP initialize/tools-call via stdio, wrong tenant rejection, missing ticket, startup and malformed-result errors. Install/pin resolved dependencies and save fixture hashes.
- [x] **2. Retrieval and answers:** Write `tests/test_retrieval.py` for scope/status/date/authority, missing/contradictory evidence and 14-day boundaries. Implement `select_documents(documents, tenant, query, today) -> Retrieval` and `assess(ticket, retrieval, today) -> Evidence`; `ModelAdapter.render(evidence, preferences, draft=False) -> str` has no tools. Verify current policy and billing citations, no unconditional promise, and hostile text cannot become instructions.
- [x] **3. Durable memory:** Write `tests/test_memory.py` for reopened databases, corrections by dimension, scope, quoted/incidental text, and forget after restart. Implement SQLite `Store`, `Memory` and bounded explicit extraction. Interfaces: `preferences(context)`, `upsert_preferences(context, updates)`, `forget(context)`; store source session and UTC timestamps, no raw messages/cache.
- [x] **4. Actions:** Write `tests/test_actions.py` against real HTTP/MCP for draft-only, owner/hash checks, concurrent/repeated approval, stale/racing 409, 503 before/after, uncertain reconciliation and restart. Implement immutable proposal storage, approval records, conditional transitions and `Actions.propose/approve/reconcile/inspect`. Persist consent and intended write before side effect; validate backend receipts against exact proposal. Use only test-bootstrap failure injection.
- [x] **5. API and security:** Write `tests/test_security.py` and API contract tests. Implement `orchestrator.py`, `api.py`; POST respond, approve, forget and reconcile plus scoped action/memory inspection. Trace every request with redacted IDs/stages/timing. Verify no prose approval, foreign policy/tool/memory access, hostile document effect, missing ticket or malformed input write.
- [x] **6. Evaluator and delivery:** Write scenario runner contract test. Implement actual subprocess fixture/API evaluation harness loading unchanged `eval_scenarios.json`, including full app-process memory restart, fixture resets, E01–E08 evidence, generated traces/report and exit-on-failure. Add candidate README, reproducible setup/run/demo commands and dependency lock. Run all backend/project tests, evaluator, HTTP demo and fixture-integrity audit; perform independent final code review and repair significant findings with regression tests.

Each implementation task follows test failure → implementation → smallest relevant passing run. Record execution evidence in `docs/IMPLEMENTATION_LOG.md`. Final review checks each global constraint against executed state, not response keywords alone.

