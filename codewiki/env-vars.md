# Environment Variables

The application reads environment variables only; `.env.example` is documentation and is **not** auto-loaded. Relative paths resolve against the project root.

## Assistant API (`assistant/config.py` → `Settings.from_env`)

| Variable | Default | Purpose |
|---|---|---|
| `CONTEXTPILOT_API_URL` | `http://127.0.0.1:8765` | Fixture backend base URL (http/https, no query, fragment or credentials). Also passed to the MCP server. |
| `CONTEXTPILOT_DB` | `var/assistant.sqlite3` | SQLite file for preferences, actions, approvals |
| `CONTEXTPILOT_TRACE` | `var/traces.jsonl` | JSONL trace file |
| `CONTEXTPILOT_MCP_SERVER` | `mcp_server.py` | Path of the MCP server script, launched with the same interpreter |
| `CONTEXTPILOT_TODAY` | unset → current UTC date | Demo/evaluation only: pin the policy clock (`YYYY-MM-DD`). Invalid values fail at startup. |

## Code-level settings (no env var)

| `Settings` field | Default | Constraint |
|---|---|---|
| `mcp_timeout` | 8.0 s | > 0; open timeout and per-call timeout |
| `http_timeout` | 3.0 s | > 0 |
| `read_attempts` | 2 | 1 or 2 |
| `publish_attempts` | 2 | 1 or 2 (bounded on purpose) |
| `proposal_ttl_hours` | 24.0 | > 0; drafts older than this cannot be approved |
| `python` | current interpreter | used to spawn the MCP server |

## Fixture processes

| Variable | Used by | Purpose |
|---|---|---|
| `QUIET=1` | `server.py` | suppress request logs |
| `CONTEXTPILOT_API_URL` | `mcp_server.py` | backend base URL (set by `TicketMCP`) |
| `PYTHONUTF8=1` | MCP child, eval/test children | consistent UTF-8 on Windows |
| `PYTEST_DEBUG_TEMPROOT` | pytest (set in `tests/conftest.py`) | fresh per-run temp root |

There are no secrets or API keys: the model adapter is deterministic and offline.
