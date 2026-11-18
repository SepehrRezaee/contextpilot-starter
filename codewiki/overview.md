# ContextPilot — Product Overview

> All code paths in this wiki are relative to the `contextpilot-starter/` project root.

## What it is

ContextPilot is an internal AI assistant for multi-tenant support operators. It investigates support tickets over a real MCP tool, cites the **current** tenant policy, remembers explicit writing preferences across sessions, and drafts customer replies — but publishes a reply **only** after a separate human approval bound to the exact text.

It was built as a 5-hour Mohaymen ICT Group Applied AI Engineering exercise (November 2026). The project deliberately favours a narrow, verifiable system over an elaborate one.

## Target users

| User | What they do |
|---|---|
| Support operator (e.g. `reviewer-01`) | Asks about tickets and policies, sets writing preferences, reviews drafts, approves publication |
| Tenant (customer organisation) | `northstar` (tickets `NS-*`) and `harbor` (tickets `HB-*`); each sees only its own data |
| Reviewer / evaluator | Runs the test suite and the E01–E08 evaluation against real processes |

## Core capabilities

1. **Grounded answers** — metadata-filtered, lexically ranked policy retrieval with document-ID citations; insufficient or conflicting evidence escalates instead of guessing.
2. **Real MCP integration** — `get_ticket` via the official Python MCP SDK over stdio; tool output is validated data, never instructions.
3. **Durable, scoped memory** — explicit writing preferences (length, format, introductions) in SQLite, per tenant and user, with provenance, correction and forgetting.
4. **Approval gate** — immutable proposals with a SHA-256 body hash and stable idempotency key; publication requires a separate, hash-bound approval.
5. **Recoverable writes** — 503s and timeouts are settled by looking up the same idempotency key; unclear outcomes stay `UNKNOWN` rather than being guessed.
6. **Redacted tracing** — one JSON line per request with IDs, tools, documents, action state, errors and stage timings; no prompts or bodies.

## Design principle

> **Authority never comes from text.** Which tool runs, for which tenant, and whether anything is published is decided by code and trusted context only. Documents, tool output and model output are data.

## Scope boundaries (deliberate)

- **Model:** a deterministic renderer (`DeterministicAdapter`); no live LLM or API key. A real model would sit behind the same `ModelAdapter` interface with no tool or approval authority.
- **Identity:** tenant/user/session come from request JSON as *trusted local context* — not production authentication.
- **Runtime:** single host, one API worker, SQLite; no UI, OAuth, vector DB or cloud deployment.

## Source material

| Source | Location |
|---|---|
| Candidate assignment (7 pages) | `D:\Mohaymen\Mohaymen_ContextPilot_Candidate_Assignment.pdf` |
| Candidate README (setup, walkthrough, guarantees) | `assistant/README.md` |
| Decision log | `DECISIONS.md` |
| Fixture README (backend contract) | `README.md` |
| Evaluation results | `artifacts/eval_results.json` |

## Related pages

[Architecture](architecture.md) · [User flows](user-flows/index.md) · [API reference](api-reference.md) · [Data models](data-models.md) · [Commands](commands.md) · [Environment variables](env-vars.md) · [Glossary](glossary.md)
