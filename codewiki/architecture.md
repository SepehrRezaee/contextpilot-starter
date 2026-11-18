# Architecture

## System Overview

```text
                 operator (HTTP JSON, trusted tenant/user/session)
                                   │
                                   ▼
┌──────────────────────── Assistant API  :8000  (assistant/) ────────────────────────┐
│ api.py ──► Orchestrator.respond()                                                    │
│              │ routing (operator message only)                                       │
│              ├─► TicketMCP ───────────── stdio ──► mcp_server.py ──HTTP──┐           │
│              ├─► BackendReader ─────── HTTP GET documents / actions ─────┤           │
│              ├─► retrieval.select_documents / assess                      │           │
│              ├─► Store (SQLite: preferences)                              │           │
│              ├─► DeterministicAdapter.render (text only)                  │           │
│              └─► Actions.propose / approve / reconcile                    │           │
│                     └─► CommentPublisher ── HTTP POST comment ────────────┤           │
│ Trace ──► var/traces.jsonl          Store ──► var/assistant.sqlite3        │           │
└───────────────────────────────────────────────────────────────────────────┼───────────┘
                                                                            ▼
                                              Fixture backend :8765 (server.py, in-memory)
```

## Architecture Type

Single app: one FastAPI deployable with internal modules, plus two external fixture processes supplied by the exercise (HTTP backend and MCP server). No frontend, mobile app or message broker.

## Components

### Assistant API

- **Tech stack**: Python 3.11, FastAPI 0.133.1, Pydantic 2.13.4, HTTPX 0.28.1, MCP SDK 1.26.0, AnyIO 4.12.1, Uvicorn 0.41.0, SQLite (stdlib)
- **Location**: `assistant/`
- **Entry point**: `assistant/api.py` → `create_app()` (Uvicorn `--factory`)
- **Purpose**: routing, grounded retrieval, memory, approval-gated actions, tracing

| Module | Responsibility |
|---|---|
| `api.py` | Endpoints, Pydantic validation, trace + error mapping (`execute`) |
| `orchestrator.py` | Deterministic routing and the respond workflow |
| `schemas.py` | `Context`, `Ticket`, `Document`; tenant registry `TENANTS`; ticket namespace check |
| `mcp_client.py` | `TicketMCP`: persistent stdio MCP session, `get_ticket`, output validation |
| `lifecycle.py` | `LoopResource`: one async context manager held open per event loop |
| `backend_client.py` | `HttpPool`, `BackendReader` (reads + retry), `CommentPublisher` (only write path) |
| `retrieval.py` | Document filtering/ranking, policy-text parsing, `Evidence` assessment |
| `model_adapter.py` | `ModelAdapter` protocol, `DeterministicAdapter.render` |
| `memory.py` | Strict grammars: `extract_preferences`, `parse_forget` |
| `storage.py` | `Store`: one SQLite connection, preferences |
| `actions.py` | Proposal/approval state machine, publish, reconciliation |
| `tracing.py` | Redacted JSONL `Trace` |
| `config.py` | `Settings` and `from_env` |
| `errors.py` | `AssistantError(code, status)` — content-free error codes |
| `eval_runner.py` | E01–E08 evaluation over real subprocesses |

### Fixture backend (supplied, unchanged)

- **Tech stack**: Python stdlib `http.server`
- **Location**: `server.py`, data in `fixtures.json`
- **Purpose**: documents, tickets with optimistic versions, comment writes with idempotency dedup, action receipts, failure switches. State is in memory; `POST /__admin/reset` restores it.

### Fixture MCP server (supplied, unchanged)

- **Tech stack**: official MCP SDK `FastMCP`, stdio transport
- **Location**: `mcp_server.py`
- **Purpose**: one read-only tool `get_ticket(tenant_id, ticket_id)` that GETs the backend. No authentication.

## Component Communication

| From | To | Protocol | Purpose |
|---|---|---|---|
| Assistant | MCP server | MCP over stdio (spawned once, reused; reopened once if broken) | Read tickets |
| MCP server | Backend | HTTP GET | Ticket lookup |
| Assistant (`BackendReader`) | Backend | HTTP GET | Documents, action receipts by idempotency key |
| Assistant (`CommentPublisher`) | Backend | HTTP POST | Publish an approved comment (only from `Actions`) |

There is no HTTP fallback for ticket reads: if MCP fails the request fails with `mcp_unavailable`.

## Shared Infrastructure

| Store | Location | Contents |
|---|---|---|
| SQLite (WAL, `synchronous=FULL`) | `var/assistant.sqlite3` | preferences, actions, approvals |
| JSONL traces | `var/traces.jsonl` | one redacted record per request |
| Evaluation state | `var/evaluation/<run>/`, `artifacts/` | per-scenario DBs/logs, reports, sample traces |

## Authentication & Authorization

- **Identity**: `tenant_id`, `user_id`, `session_id` in request JSON/query, validated by Pydantic (`extra="forbid"`). Treated as trusted application claims for the local demo — anyone who can send JSON can impersonate an operator.
- **Tenant scope**: tickets must match the tenant's prefix (`NS-`/`HB-`); prose naming another tenant is declined before any tool call; MCP arguments always use `context.tenant_id`; MCP results are rejected if their tenant/id differ.
- **Ownership**: actions and memory are read only with matching tenant + user; foreign lookups return 404.
- **Consent**: publishing requires `POST /approve` with the stored `body_hash`; the server generates the `approval_reference`. Prose such as "send it" never publishes.
- **Production path**: derive claims from authenticated tokens; enforce authorization in the backend and an authenticated MCP transport.

## Data Flow

**Investigate / draft:** validate context → route → (decline / memory ops) → MCP `get_ticket` → GET documents → `select_documents` (tenant/global, active, effective date, topic, conflict checks) → `assess` (calendar days, window, missing facts) → render with stored style → (draft) `Actions.propose` stores an immutable `PROPOSED` action → trace.

**Approve:** owner-scoped load + integrity checks → hash compare → TTL and MCP version preflight → one transaction writes the approval row and `SENDING` → POST with stored body/key → validate receipt → `COMMITTED`; on 503/timeout look up the same key → `COMMITTED`, retry with the same key, or `UNKNOWN`.

## Action State Machine

```text
PROPOSED ──approve(exact hash, fresh, version unchanged)──► SENDING
PROPOSED ──ticket changed / draft expired─────────────────► CONFLICT   (no consent recorded)
SENDING  ──valid receipt──────────────────────────────────► COMMITTED
SENDING  ──409 version_conflict────────────────────────────► CONFLICT
SENDING  ──other 409 / receipt mismatch────────────────────► INVARIANT_VIOLATION
SENDING  ──other 4xx──────────────────────────────────────► FAILED
SENDING  ──ambiguous, unconfirmed by key──────────────────► UNKNOWN
UNKNOWN  ──reconcile finds key────────────────────────────► COMMITTED
UNKNOWN  ──approve again, key still absent────────────────► SENDING (same key)
```

Guarantee: durable consent, stable-key deduplication and reconciliation. It is **not** formal exactly-once delivery.

## Performance Notes

Measured with `scripts/benchmark.py` on Windows. The submitted version opened an MCP session per read (ticket investigation p50 ≈ 1.4 s). The current version keeps one MCP session open: the first ticket read costs ≈ 1.6 s (process startup), later reads ≈ 10–30 ms. HTTP uses one pooled sync client in worker threads; SQLite uses one long-lived connection.
