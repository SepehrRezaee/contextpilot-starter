# ContextPilot: candidate solution

ContextPilot is a local JSON API for support operators. It investigates a ticket through the **supplied stdio MCP server**, grounds refund answers in the tenant's current policy with document-ID citations, remembers explicit writing preferences across sessions and restarts, and drafts customer replies. A draft is published only after a **separate approval request bound to the exact stored body hash**. The default deterministic mode runs offline. [Live mode](#model-adapter) uses AvalAI `deepseek-v4.1-flash` to compose answers and drafts.

## What is supplied and what is mine

| Supplied fixture (unchanged) | Candidate implementation |
|---|---|
| `server.py`, `mcp_server.py`, `fixtures.json`, `eval_scenarios.json`, `test_mock_api.py`, root `README.md` (fixture docs), `START_HERE.txt` | `assistant/` (code), `tests/`, `artifacts/` (generated evidence), `DECISIONS.md`, `pyproject.toml`, `requirements.lock`, `.env.example` |

`tests/test_scenarios.py` checks the seven supplied files against the SHA-256 hashes in `artifacts/fixture_manifest.json`. Candidate code never imports fixture modules.

| Module | Responsibility |
|---|---|
| `api.py` | FastAPI endpoints; validates the trusted `tenant_id`/`user_id`/`session_id` context |
| `orchestrator.py` | One bounded, deterministic workflow: scope check → forget/remember/recall → investigate/draft |
| `mcp_client.py`, `lifecycle.py` | Official MCP SDK stdio client; one persistent session, bounded timeouts and retries |
| `backend_client.py` | Read-only document/action-status reader and the approval-only comment publisher |
| `retrieval.py` | Metadata filtering, lexical ranking, bounded policy parsing, evidence assessment |
| `memory.py`, `storage.py` | Strict preference/forget grammars; SQLite preferences and action ledger |
| `actions.py` | Immutable proposals, hash-bound approval, idempotent publish and reconciliation |
| `model_adapter.py` | `ModelAdapter` interface, deterministic renderer, and async AvalAI renderer with output validation |
| `tracing.py`, `eval_runner.py` | Redacted JSONL traces; E01–E08 evaluator |

## Setup

Python 3.11+ (verified with 3.11.15). Pinned runtime: MCP SDK **1.26.0**, FastAPI **0.133.1**, Pydantic **2.13.4**, HTTPX **0.28.1**, Uvicorn **0.41.0**, AnyIO **4.12.1**; tests use pytest **8.3.5**. `requirements.lock` pins every transitive dependency. Run all commands from the repository root:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock -e '.[test]'
```

On macOS/Linux, use `.venv/bin/python` instead of `.\.venv\Scripts\python.exe`.

## Run

```powershell
# Terminal 1: the supplied backend
.\.venv\Scripts\python.exe server.py --port 8765

# Terminal 2: deterministic mode with the optional evaluation date pin
$env:CONTEXTPILOT_MODEL_MODE = 'deterministic'
$env:CONTEXTPILOT_TODAY = '2026-11-16'
.\.venv\Scripts\python.exe -m uvicorn assistant.api:create_app --factory --host 127.0.0.1 --port 8000 --no-access-log
```

The assistant starts `mcp_server.py` with the same interpreter, initializes one stdio session and reuses it for every `get_ticket` call. `.env.example` documents the environment variables. The [live startup command](#model-adapter) loads settings from `.env` into the process environment. When `CONTEXTPILOT_TODAY` is unset, policy dates use the current UTC date. Interactive schemas are at `http://127.0.0.1:8000/docs`.

Quick check without PowerShell:

```sh
curl -s -X POST http://127.0.0.1:8000/assistant/respond -H 'Content-Type: application/json' \
  -d '{"tenant_id":"northstar","user_id":"reviewer-01","session_id":"s1","message":"Explain NS-104"}'
```

## Walkthrough (PowerShell)

```powershell
$api = 'http://127.0.0.1:8000'
$context = @{tenant_id='northstar'; user_id='reviewer-01'; session_id='review-a'}
function Ask-Assistant([string]$message) {
    $payload = $context.Clone(); $payload.message = $message
    Invoke-RestMethod "$api/assistant/respond" -Method Post -ContentType 'application/json' -Body ($payload | ConvertTo-Json)
}

# 1. Investigation: real MCP get_ticket, current policy, citations, missing facts.
Ask-Assistant 'Explain NS-104 and cite the current refund policy' | ConvertTo-Json -Depth 8

# 2. Save, recall in a new session, then correct one preference.
Ask-Assistant 'I prefer concise customer replies without unnecessary introductions.'
$context.session_id = 'review-b'
Ask-Assistant 'Please use bullet points instead of short paragraphs from now on.'
Ask-Assistant 'What customer communication preferences do you remember?'
Ask-Assistant 'Actually, use detailed replies from now on.'

# 3. Draft: stored as PROPOSED. Nothing is posted to the ticket.
$draft = (Ask-Assistant 'Draft a response for NS-104').proposed_action
$draft | ConvertTo-Json -Depth 8
Invoke-RestMethod "$api/assistant/actions/$($draft.action_id)?tenant_id=northstar&user_id=reviewer-01&session_id=review-b"

# 4. Separate, explicit approval of that exact body hash publishes it once.
$approval = $context.Clone(); $approval.body_hash = $draft.body_hash
Invoke-RestMethod "$api/assistant/actions/$($draft.action_id)/approve" -Method Post -ContentType 'application/json' -Body ($approval | ConvertTo-Json)

# 5. Read-only reconciliation; repeating it (or the approval) never publishes again.
Invoke-RestMethod "$api/assistant/actions/$($draft.action_id)/reconcile" -Method Post -ContentType 'application/json' -Body ($context | ConvertTo-Json)

# 6. Forget, then recall again (restart the assistant first to see that it persists).
Invoke-RestMethod "$api/assistant/users/reviewer-01/memory/forget" -Method Post -ContentType 'application/json' -Body ($context | ConvertTo-Json)
Ask-Assistant 'What customer communication preferences do you remember?'
```

Saying `Forget my writing preferences.` also deletes everything, and `Forget my format preferences.` deletes one dimension. A negated sentence like `Do not forget my preferences.` deletes nothing. `GET /assistant/users/{user_id}/memory` lists stored preferences, given the same three context query parameters. The investigation answer is for the operator and puts a `[document ID]` after each claim. The draft body is customer wording without internal IDs; its citations are returned in `citations` and `evidence.claims`. Always check the returned action `status`: `CONFLICT`, `UNKNOWN` and `FAILED` are explicit outcomes even with HTTP 200.

## Tests and evaluation

```powershell
.\.venv\Scripts\python.exe -m unittest -v test_mock_api.py   # supplied backend smoke tests
.\.venv\Scripts\python.exe -m pytest -q                      # offline suite, including supplied smoke tests and E01-E08
.\.venv\Scripts\python.exe -m assistant.eval_runner          # E01-E08 only; rewrites artifacts/
.\.venv\Scripts\python.exe -m pytest tests/test_actions.py -v  # approval, 503 before/after commit, 409, restart recovery
```

The offline suite uses a real fixture HTTP server, the SDK stdio MCP server, and subprocess restarts. Adapter tests use mocked AvalAI HTTP responses. The evaluator loads the supplied `eval_scenarios.json` and uses deterministic rendering. For each scenario it starts an isolated API process with its own database, resets the fixture, and checks backend comment counts, SQLite rows, trace events and process restarts. E06 injects an after-commit HTTP 503 through the test bootstrap. The evaluator and date-dependent tests pin the policy clock to **2026-11-16**. Each pytest run gets a fresh ignored `tmp/pytest-run-*` directory and runs with the cache provider disabled to avoid Windows multi-account permission errors.

## Evidence in `artifacts/` (regenerated by the evaluator)

- `eval_results.json`: E01–E08 pass/fail, durations, failure reasons and the independent evidence checked.
- `evaluation_traces.jsonl`: one redacted trace per evaluation request.
- `trace_investigation.json`: sample trace of a ticket investigation (MCP initialize and `get_ticket`, document reads, selected IDs, latency).
- `trace_503_recovery.json`: sample trace of approval → `SENDING` → HTTP 503 → reconciliation by the same idempotency key → `COMMITTED`.

The evaluator generates these artifacts in deterministic mode. Each trace has `trace_id`, tenant/user/session IDs, intent, tool names, selected document IDs, action ID and state transitions, error codes, per-stage `elapsed_ms` and `duration_ms`. API processes write redacted traces and local state to `var/traces.jsonl` and `var/assistant.sqlite3` by default.

## Safety model

- **Identity.** The JSON context fields stand in for authenticated claims on localhost. **This is not authentication**: anyone who can call the API can claim any operator. Within that assumption, every read and write is scoped to the tenant, and memory and actions are also scoped to the user. Prose that names another tenant or a foreign ticket ID is declined before any tool call. MCP calls always pass the trusted `tenant_id`, and the MCP result is checked against schema and identity.
- **Grounding.** Documents are filtered by tenant or global scope, `active` status and effective date. Policy topics come from titles; a tenant policy beats a global one, the newest effective date wins, and lexical overlap only breaks ties. A bounded parser extracts the refund window and conditions. Conflicting or unsupported policy language yields `insufficient_evidence`. No answer or draft promises a refund. The hostile `N-OPS-NOTE-2026` is retrieved as data but never cited, and it cannot call tools, store memory or approve anything.
- **Memory.** One SQLite row per tenant, user and dimension (length, format, introductions) stores the value, source session and timestamps. Only exact first-person durable statements are stored. Quoted, temporary, compound or negated text is not. A correction overwrites only its own dimension. Forgetting deletes rows, and there is no cache or transcript fallback. This is application-level deletion, not secure erasure.
- **Actions.** A proposal stores the exact body, SHA-256, ticket version, owner and a stable idempotency key. SQLite triggers make it immutable. Approval requires the owner and the exact hash. The TTL and the current ticket version (via MCP) are checked first, then consent and `SENDING` are committed in one transaction before the POST. A stale version or HTTP 409 means `CONFLICT`, which needs a new draft. An unclear result is settled by looking up the **same** key. If it is not found, the same body is retried once with the same key. If the outcome is still unclear, the action stays `UNKNOWN`. Reconciliation is read-only, and a restart never republishes on its own. This gives durable consent and stable-key deduplication, **not a formal exactly-once guarantee**.

## Model adapter

Deterministic generation is the default and requires no credentials. For live mode, store `AVALAI_API_KEY` in your Git-ignored `.env` file. Run the synthetic backend as shown above, then start the API in a second PowerShell window with Uvicorn loading that file into its environment.

```powershell
$env:CONTEXTPILOT_MODEL_MODE = 'avalai'
$env:CONTEXTPILOT_TODAY = '2026-11-16'  # Optional fixture date
.\.venv\Scripts\python.exe -m uvicorn assistant.api:create_app --factory --host 127.0.0.1 --port 8000 --no-access-log --env-file .env
```

The adapter calls `https://api.avalai.ir/v1/chat/completions` with `deepseek-v4.1-flash` using async HTTPX and non-thinking mode. It sends structured evidence, approved passages, and the current user's scoped writing preferences to AvalAI. The provider can arrange those passages and choose approved framing. The app validates every returned passage before delivering the response or storing a proposal. Claim accuracy still depends on the upstream evidence and policy parser. Live responses use a bounded English format.

`/health` reports the configured adapter. A successful `model.render` trace event records the provider, requested model, latency, HTTP status, attempts, and usage when AvalAI supplies it. Transient errors get one retry. Provider failures or rejected output return a sanitized error and stop before proposal creation.

The opt-in live tests make a short paid smoke request and exercise the local API and MCP against the synthetic backend. They check citations, cross-session preferences, the proposal hash, tenant isolation, duplicate approval, and recovery from a synthetic after-commit HTTP 503. The command below loads `.env` into the test process.

```powershell
$env:CONTEXTPILOT_LIVE_TESTS = '1'
.\.venv\Scripts\python.exe -m dotenv --file .env run --no-override -- .\.venv\Scripts\python.exe -m pytest -q -s tests/test_avalai_live.py
Remove-Item Env:CONTEXTPILOT_LIVE_TESTS
```

To return to offline generation, set `$env:CONTEXTPILOT_MODEL_MODE = 'deterministic'` and restart the API with the original startup command above.

## Limitations

Single host, one API worker, local trusted context, no production OAuth or service-side ticket authorization, no background worker for `UNKNOWN` actions (an operator reconciles or re-approves), English-only bounded grammars, and no UI. See `../DECISIONS.md` for the reasoning and the first production improvement.
