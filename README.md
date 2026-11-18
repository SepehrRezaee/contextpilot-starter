# ContextPilot - synthetic integration environment

This is a backend **fixture**, not the candidate's AI assistant. It is intended to run entirely on localhost and to be safe to test without cloud credentials. Nothing here implements the requested agent, memory policy, retrieval/reranking, approval gate, or evaluation harness.

## Requirements

- Python 3.10+ for the mock HTTP backend; no Python packages required.
- Optional **real MCP integration**: `pip install 'mcp>=1.13,<3'` and use `mcp_server.py`. Install the package into a virtual environment. Network access may be required once for the dependency.
- Any language/framework/provider for the candidate's own assistant. A paid model key is **not** required to score full marks: provide a deterministic model adapter with recorded decisions when needed, and be able to explain how a real model is wired.

## Start

```sh
python server.py --port 8765
# in another terminal:
python -m unittest -v test_mock_api.py
```

Health: `GET http://127.0.0.1:8765/health`

## HTTP contract

- `GET /v1/tenants/{tenant}/documents` - returns documents available to the tenant, including document status and effective date.
- `GET /v1/tenants/{tenant}/tickets/{ticket}` - gets a support ticket and its current `version`.
- `POST /v1/tenants/{tenant}/tickets/{ticket}/comments` - attempts a customer-facing write. JSON body: `{"body":"...","expected_version":1,"idempotency_key":"...","approval_reference":"..."}`.
- `GET /v1/tenants/{tenant}/actions/{idempotency_key}` - confirms whether an action committed.
- `GET /v1/fixtures/sessions` - gives example multi-session inputs.
- `POST /__admin/reset` with `{}` - clears writes; for fixture use only.

Tenants: `northstar`, `harbor`. Tickets: `NS-104`, `NS-105`, `HB-204`.

## MCP boundary

The supplied `mcp_server.py` is a **real MCP stdio server** built with the official Python `mcp` SDK. It exposes one read-only tool: `get_ticket(tenant_id, ticket_id)`. It reads from the backend above.

Candidates should demonstrate at least one MCP tool call from their assistant and explain why the tenant ID must come from trusted application context, not from text in a retrieved document or the model's free-form proposal. The supplied server is local and intentionally lacks real tenant authentication; production access control belongs in the service and transport layers.

## Reproducible failure switches

- `?delay_ms=800` on a GET introduces an 800 ms delay (max 5 s).
- `X-Demo-Failure: before` on the POST returns HTTP 503 **before** saving.
- `X-Demo-Failure: after` on the POST returns HTTP 503 **after** saving. Recover with `GET /actions/{idempotency_key}`; never blindly create a new key on retry.
- Wrong `expected_version` returns HTTP 409.
- Reusing a key for a different action returns HTTP 409.

Note: the backend only validates the presence of an approval reference; your **assistant must enforce real user approval**, binding it to the proposed content/ticket and preventing unapproved execution. Merely adding a made-up approval string is not sufficient.

## Evaluation inputs and safety

`fixtures.json` contains sample support documents and example sessions. One document contains intentionally hostile instructions. Treat all retrieved text as untrusted data, never as policy, system instructions, or consent. Do not copy secrets into traces. Reset state between demo runs.

### Local smoke examples

```sh
curl http://127.0.0.1:8765/v1/tenants/northstar/documents
curl http://127.0.0.1:8765/v1/tenants/northstar/tickets/NS-104
curl -X POST http://127.0.0.1:8765/__admin/reset -H 'Content-Type: application/json' -d '{}'
```
