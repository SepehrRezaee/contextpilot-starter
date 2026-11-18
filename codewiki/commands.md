# Commands

Run from the `contextpilot-starter/` root. On macOS/Linux use `.venv/bin/python` instead of `.\.venv\Scripts\python.exe`.

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock -e '.[test]'
```

Requires Python 3.11+ (verified 3.11.15) and package-index access once.

## Run

| Purpose | Command |
|---|---|
| Fixture backend | `.\.venv\Scripts\python.exe server.py --port 8765` (`QUIET=1` silences logs) |
| Assistant API | `.\.venv\Scripts\python.exe -m uvicorn assistant.api:create_app --factory --host 127.0.0.1 --port 8000 --no-access-log` |
| Pin policy date for a demo | `$env:CONTEXTPILOT_TODAY = '2026-11-16'` before starting the API |
| Reset fixture state | `Invoke-RestMethod http://127.0.0.1:8765/__admin/reset -Method Post -ContentType 'application/json' -Body '{}'` |

## Test

| Purpose | Command | Time |
|---|---|---|
| Full suite (70 tests, incl. starter smoke + E01–E08) | `.\.venv\Scripts\python.exe -m pytest -q` | ~70–80 s |
| Fast suite (skip process-based scenarios) | `.\.venv\Scripts\python.exe -m pytest -q tests --ignore=tests/test_scenarios.py` | ~40 s |
| Retrieval / memory only | `.\.venv\Scripts\python.exe -m pytest -q tests/test_retrieval.py tests/test_memory.py` | < 3 s |
| Recovery demo | `.\.venv\Scripts\python.exe -m pytest tests/test_actions.py -k 503 -v` | ~5 s |
| Starter smoke tests | `.\.venv\Scripts\python.exe -m unittest -v test_mock_api.py` | < 1 s |
| E01–E08 evaluation (writes `artifacts/`) | `.\.venv\Scripts\python.exe -m assistant.eval_runner` | ~30 s |
| Benchmark | `.\.venv\Scripts\python.exe scripts/benchmark.py [--root PATH] [--iterations 20] [--concurrency 8] [--output FILE]` | minutes |

pytest notes: `addopts = -ra -p no:cacheprovider`; each run uses a fresh `tmp/pytest-run-*` root. Cache-dependent options (`--lf`, `--ff`) need `-o addopts=-ra` and a writable `cache_dir`.

## Test map

| File | Covers |
|---|---|
| `tests/test_mcp_integration.py` | real stdio MCP, foreign/missing tickets, startup failure, malformed results, timeout |
| `tests/test_retrieval.py` | filters, current vs superseded, calendar boundaries, conflicts, unsupported wording, style rendering |
| `tests/test_memory.py` | persistence across processes, correction, forget, preference and forget grammars |
| `tests/test_actions.py` | immutable drafts, 503 before/after, concurrency, stale version, 409 race, UNKNOWN, restart recovery, length limits |
| `tests/test_security.py` | API grounding, foreign prose, hash-bound approval, owner scope, input validation, chat forget, tenant registry |
| `tests/test_config.py` | `CONTEXTPILOT_TODAY` pinning and validation |
| `tests/test_scenarios.py` | E01–E08 via `eval_runner`; starter files unchanged (SHA-256 manifest) |
| `tests/test_pytest_portability.py` | Windows multi-account temp/cache robustness |

## Outputs

| Path | Produced by |
|---|---|
| `var/assistant.sqlite3`, `var/traces.jsonl` | running API |
| `artifacts/eval_results.json`, `evaluation_traces.jsonl`, `trace_investigation.json`, `trace_503_recovery.json` | `eval_runner` |
| `var/evaluation/<run>/` | per-scenario DBs and logs |
| `tmp/pytest-run-*` | pytest |
