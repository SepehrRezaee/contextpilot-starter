# ContextPilot candidate solution

This candidate API investigates synthetic support tickets through **real SDK stdio MCP**, selects current tenant policy over HTTP, remembers explicit writing preferences in SQLite, and proposes customer replies. Only a separate owner approval for the exact stored draft can publish a comment. It runs completely offline after dependency installation; no model key is needed.

The seven root starter files are unchanged infrastructure. The root README documents the fixture; this README documents the candidate. `artifacts/fixture_manifest.json` records their original SHA-256 hashes. Candidate runtime code is in `assistant/`, tests in `tests/`, and design assumptions in `../DECISIONS.md`.

## Setup

Run from the workspace root, with Python 3.11+ (verified with 3.11.15). A clean environment needs package-index access once. Runtime versions: MCP **1.26.0**, FastAPI **0.133.1**, Pydantic **2.13.4**, HTTPX **0.28.1**, Uvicorn **0.41.0**, AnyIO **4.12.1**; tests use pytest **8.3.5**. All resolved transitive dependencies are pinned in `requirements.lock` (Windows-only dependencies have platform markers).

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock -e '.[test]'
```

On macOS/Linux, replace `.\.venv\Scripts\python.exe` with `.venv/bin/python` in all commands. No fixture modules are imported by candidate code or candidate tests.

## Start the fixture and assistant

In terminal 1:

```powershell
.\.venv\Scripts\python.exe server.py --port 8765
```

In terminal 2:

```powershell
.\.venv\Scripts\python.exe -m uvicorn assistant.api:create_app --factory --host 127.0.0.1 --port 8000 --no-access-log
```

Open `http://127.0.0.1:8000/docs` for request schemas. Configuration is documented in `.env.example`; export the variables in the terminal (the file is not auto-loaded). Defaults resolve relative to the project root, including the supplied MCP script, SQLite DB and JSONL trace path. The assistant starts the supplied MCP server once with the same venv interpreter, initializes a genuine stdio session and reuses it for ticket reads; a session that breaks is reopened once. No HTTP ticket-read fallback exists.

`/health` reports process readiness and adapter mode, not upstream availability. Default policy time is the current UTC date. Tests/evaluation inject **2026-11-16**, so the demo remains reproducible when the real date changes. To reproduce those date-dependent answers in the live API, set `$env:CONTEXTPILOT_TODAY = '2026-11-16'` before starting Uvicorn (demo/evaluation only; an invalid date fails at startup).

## Reviewer walkthrough (PowerShell)

The following helper only sends individual explicit API requests. Inspect the returned draft before running the separate approval command.

```powershell
$api = 'http://127.0.0.1:8000'
$context = @{tenant_id='northstar'; user_id='reviewer-01'; session_id='review-a'}
function Ask-Assistant([string]$message) {
    $payload = $context.Clone()
    $payload.message = $message
    Invoke-RestMethod "$api/assistant/respond" -Method Post -ContentType 'application/json' -Body ($payload | ConvertTo-Json)
}

# 1. Real MCP investigation with current policy IDs and verification gaps.
Ask-Assistant 'Explain NS-104 and cite the current refund policy' | ConvertTo-Json -Depth 8

# 2. Explicit preference; new session; independent format correction.
Ask-Assistant 'I prefer concise customer replies without unnecessary introductions.'
$context.session_id = 'review-b'
Ask-Assistant 'Please use bullet points instead of short paragraphs from now on.'
Ask-Assistant 'What customer communication preferences do you remember?'
Ask-Assistant 'Actually, use detailed replies from now on.'

# 3. Draft: no customer comment is posted. Inspect the exact stored body/hash.
$result = Ask-Assistant 'Draft a response for NS-104'
$draft = $result.proposed_action
$draft | ConvertTo-Json -Depth 8
$query = 'tenant_id=northstar&user_id=reviewer-01&session_id=review-b'
Invoke-RestMethod "$api/assistant/actions/$($draft.action_id)?$query"

# 4. After reviewing that exact body, make a SEPARATE approval request.
$approval = $context.Clone()
$approval.body_hash = $draft.body_hash
Invoke-RestMethod "$api/assistant/actions/$($draft.action_id)/approve" -Method Post -ContentType 'application/json' -Body ($approval | ConvertTo-Json)

# 5. Repeat the same approval or read-only reconciliation safely.
Invoke-RestMethod "$api/assistant/actions/$($draft.action_id)/reconcile" -Method Post -ContentType 'application/json' -Body ($context | ConvertTo-Json)

# 6. Forget, restart the assistant, then recall in a new session.
Invoke-RestMethod "$api/assistant/users/reviewer-01/memory/forget" -Method Post -ContentType 'application/json' -Body ($context | ConvertTo-Json)
$context.session_id = 'review-c'
Ask-Assistant 'What customer communication preferences do you remember?'
```

You can also forget by saying `Forget my writing preferences.` (or `Delete my preferences.`); `Forget my format preferences.` deletes only that dimension, and a negated sentence such as `Do not forget my preferences.` deletes nothing. Memory inspection is `GET /assistant/users/{user_id}/memory` with the same three query context fields. Another user/tenant receives no action or memory record. Generic prose approval never publishes; the approval endpoint rejects altered hashes and arbitrary approval references.

For a deterministic 503-after-commit walkthrough, run the evaluator below. E06 launches a **separate isolated evaluation API** with a bootstrap-only failure injector, obtains a draft, explicitly calls approval, checks the backend receipt, restarts the API, repeats approval and verifies one comment. E05 proves the draft-only case. The production API accepts no failure-control request field/header or environment switch. Focused action tests also demonstrate 503-before-commit and 409 races.

## Reproduce validation and artifacts

```powershell
# Original supplied backend smoke suite (starts its own HTTP server).
.\.venv\Scripts\python.exe -m unittest -v test_mock_api.py

# All candidate tests AND the original 3 backend tests, including real MCP.
.\.venv\Scripts\python.exe -m pytest -q

# All eight official scenarios: starts/stops isolated fixture and API processes.
.\.venv\Scripts\python.exe -m assistant.eval_runner

# Focused failure/recovery demonstration.
.\.venv\Scripts\python.exe -m pytest tests/test_actions.py -v
```

Each pytest invocation creates a fresh workspace-local directory under `tmp/pytest-run-*`. This keeps the sandbox account's private pytest directories separate from a normal Windows terminal, even when both report the same username. Pytest's optional persistent cache is disabled by default to avoid reusing an inaccessible `.pytest_cache`. The normal test command above requires no permission changes or cleanup of another account's files. Cache-dependent options such as `--lf`, `--ff` and `--stepwise` require overriding the default with `-o addopts=-ra` and choosing a caller-writable `cache_dir`.

The evaluator loads the original `eval_scenarios.json`, dispatches E01–E08, resets the fixture between cases, uses separate databases, and reads sample preference sessions over HTTP. E02–E06 include actual API process restarts. It exits nonzero on any scenario failure and generates:

- `artifacts/eval_results.json`: per-scenario status, duration, failure reason and independent state evidence.
- `artifacts/evaluation_traces.jsonl`: all evaluation requests, including tool stages, sources and state transitions.
- `artifacts/trace_investigation.json`: one real investigation with current citations and stdio MCP evidence.
- `artifacts/trace_503_recovery.json`: one real approval, 503, receipt reconciliation and COMMITTED transition.

Temporary evaluation databases/logs remain under ignored `var/evaluation/`; tests use ignored `tmp/`. Production demo state uses `var/assistant.sqlite3` and `var/traces.jsonl`. Traces contain context IDs, document IDs, hashes, errors and monotonic durations, without prompts, ticket/customer bodies, or secrets.

## Boundaries and guarantees

**Identity:** JSON tenant/user/session fields simulate trusted application claims on localhost. This is not production authentication: someone who can supply arbitrary context can impersonate an operator. Production must derive claims from authentication, authorize ticket access in both backend and MCP service, and restrict direct access to the fixture-style write API. Within the demo assumption, routing, memory, documents, proposals, diagnostics and approval all enforce the same tenant/user scope. Tenant names or foreign ticket IDs in prose are declined before tool access.

**Grounding:** Metadata filters first enforce tenant/global scope, active status and effective dates. Recognized policy titles establish authority; lexical overlap ranks topical relevance, with tenant specificity and recency applied separately. Conflicting active refund constraints or equal-date policy text cause escalation. The bounded parser extracts the fixture's refund window/usage and billing checks; unsupported language is insufficient evidence. Only selected source IDs used for claims become citations. The hostile active note is encountered but cannot issue tools, store memory or consent. Calendar-day subtraction uses the injected clock; missing prior credits always prevents a refund confirmation.

**Memory:** One SQLite row per tenant/user/dimension stores value, source session and creation/update timestamps. Length and format coexist. The bounded grammar recognizes `I prefer ...` or supported style commands ending in `from now on`; quoted, temporary and unsupported prose is not persisted. Corrections such as `I prefer detailed replies, not concise replies` use the positive replacement clause. Forget deletes scoped rows; there is no preference cache, transcript retrieval or stored-summary fallback. This is application-level forgetting, not forensic secure erasure of SQLite storage. Action records and ID-only traces follow a separate audit-retention concern.

**Actions:** An immutable proposal stores exact body, SHA-256, ticket version, owner and stable idempotency key. A separate hash-bound approval is stored transactionally, with a server-generated reference, before preflight MCP version validation and SENDING. Conditional SQLite updates and a unique approval record serialize concurrent approvals. Stale or 409-conflicted actions require a new draft and new approval. HTTP writes and read-only MCP/document clients are separate capabilities; the adapter has neither.

On an ambiguous write result, action status is queried by the original key before retrying. Confirmed commit is recorded without a second publish. A not-found lookup permits a retry with the same body/key, up to two total attempts. Exhausted ambiguous attempts or unavailable verification remain UNKNOWN: a timed-out POST may still commit after a 404 lookup. `/reconcile` is owner-scoped and read-only, including after restart; an absent result there also remains UNKNOWN. Repeated approval cannot resend pending or committed actions. This provides durable consent, stable-key deduplication and reconciliation; **it is not a formal exactly-once guarantee**. Inspect the returned action `status` even when HTTP is 200; CONFLICT/UNKNOWN/FAILED are explicit outcomes.

## Deliberate limitations and evolution

The shipped `ModelAdapter` is a working deterministic renderer, not a general conversational LLM. English intent/preference and policy parsing are bounded; unsupported requests clarify or escalate. Contract/safety tests do not measure generative answer quality. A future live adapter would receive only verified evidence and scoped style; it would never receive routing, approval or publishing authority, and would need a separate probabilistic quality evaluation. No provider SDK, API key or live quality numbers are included.

This is a single-host demo with per-read MCP startup, short synchronous SQLite transactions, one API worker, no automatic retry worker for UNKNOWN state, and no production OAuth, UI or cloud deployment. Prioritize authenticated scope and service authorization, then retention/encryption and lease-based reconciliation before real customer use. The one-page decision log records trade-offs and AI-assistance validation.

