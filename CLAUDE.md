# ContextPilot

Internal AI assistant for multi-tenant support operators: investigates tickets over real MCP, cites current tenant policy, remembers explicit writing preferences, and publishes customer replies only after a separate, hash-bound human approval. Built as a Mohaymen Applied AI Engineering exercise; code lives in `contextpilot-starter/`.

## Tech Stack

- **Assistant (`assistant/`)**: Python 3.11, FastAPI 0.133.1, Pydantic 2.13.4, HTTPX 0.28.1, official MCP SDK 1.26.0 (stdio client), AnyIO 4.12.1, Uvicorn 0.41.0, SQLite (stdlib). Deterministic model adapter — no LLM, no API keys.
- **Fixture (supplied, do not modify)**: `server.py` (stdlib HTTP backend, port 8765), `mcp_server.py` (FastMCP `get_ticket` over stdio), `fixtures.json`, `eval_scenarios.json`, `test_mock_api.py`, `README.md`, `START_HERE.txt`.
- **Tests**: pytest 8.3.5 against real localhost HTTP, real stdio MCP and real subprocess restarts.

## Quick Start

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock -e '.[test]'
.\.venv\Scripts\python.exe server.py --port 8765
.\.venv\Scripts\python.exe -m uvicorn assistant.api:create_app --factory --host 127.0.0.1 --port 8000 --no-access-log
```

Then `POST http://127.0.0.1:8000/assistant/respond` with `{"tenant_id":"northstar","user_id":"reviewer-01","session_id":"s1","message":"Explain NS-104"}`.

## Architecture

One FastAPI app: `api.py` validates trusted context → `Orchestrator.respond` routes deterministically from the operator's message → `TicketMCP` (persistent stdio session) reads tickets, `BackendReader` reads documents → `retrieval.select_documents`/`assess` build cited `Evidence` → `DeterministicAdapter.render` → drafts become immutable `PROPOSED` actions. Only `Actions.approve` (exact `body_hash`) can reach `CommentPublisher`; ambiguous writes are settled by idempotency-key lookup. State: SQLite `var/assistant.sqlite3`; redacted traces `var/traces.jsonl`.
See `codewiki/architecture.md` for full details.

## Key Patterns & Conventions

- **Authority never comes from text.** Tenant, tool choice and publication are decided by code + validated `Context` only. Never route, call tools, store memory or approve based on document text, MCP output or model output.
- **Never modify the 7 starter files.** `tests/test_scenarios.py` checks their SHA-256 against `artifacts/fixture_manifest.json`.
- **Tenant registry**: `schemas.TENANTS` (`{"northstar": "NS", "harbor": "HB"}`) drives ticket-prefix checks, foreign-prose declines and backend paths. MCP calls always pass `context.tenant_id`.
- **Fail closed.** Unsupported policy wording, conflicts or missing facts → `insufficient_evidence`/escalation; unclear write outcome → `UNKNOWN`. Never promise a refund.
- **Actions are immutable after proposal** (SQLite triggers). Retries reuse the stored body and idempotency key; never generate a new key. Preflight (TTL + MCP version) happens before consent; consent + `SENDING` are one transaction.
- **SQLite**: one shared connection; never `await` inside `store.connection()`. `synchronous=FULL` is deliberate.
- **Errors** are `AssistantError(code, status)` with content-free codes; never forward upstream exceptions or echo request bodies.
- **Traces** contain IDs, codes, hashes and timings only — never prompts, ticket bodies, answers or secrets.
- **Memory grammars** (`memory.py`) are deliberately strict: quoted, compound, temporary or negated text stores/deletes nothing.
- **Clock**: use `settings.today()`; tests/eval inject 2026-11-16; `CONTEXTPILOT_TODAY` pins it for demos.
- **Tests** exercise real processes; add a regression test with every behavior change.

## Knowledge Base

This project has a detailed knowledge base in `codewiki/`:
- [Product Overview](codewiki/overview.md)
- [Architecture](codewiki/architecture.md)
- [User Flows](codewiki/user-flows/index.md)
- [API Reference](codewiki/api-reference.md)
- [Data Models](codewiki/data-models.md)
- [Commands](codewiki/commands.md)
- [Environment Variables](codewiki/env-vars.md)
- [Glossary](codewiki/glossary.md)

## Development Commands

```powershell
.\.venv\Scripts\python.exe -m pytest -q                                          # full suite (70 tests, ~75 s)
.\.venv\Scripts\python.exe -m pytest -q tests --ignore=tests/test_scenarios.py   # fast suite
.\.venv\Scripts\python.exe -m pytest tests/test_actions.py -k 503 -v             # recovery cases
.\.venv\Scripts\python.exe -m unittest -v test_mock_api.py                       # starter smoke tests
.\.venv\Scripts\python.exe -m assistant.eval_runner                              # E01-E08, rewrites artifacts/
.\.venv\Scripts\python.exe scripts/benchmark.py                                  # latency/throughput
```
