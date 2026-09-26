# Technical overview

This is a short account of how the Strategic Deal Intelligence Assistant is built, where the production path differs from the prototype, and which numbers are still unmeasured. The full design is in `docs/architecture.md`. Security controls are in `docs/security.md`.

Measured cost, latency, and evaluation figures are **TBD (measured in M7 live)**. This document does not invent them.

## Architecture summary

A FastAPI process serves the API, the UI, and the run executor. The CLI is a thin HTTP client for run and approval commands; `ingest` and `generate-slack` run in-process. Postgres 16 holds reference rows, evidence chunks, runs, approvals, briefs, spans, and `llm_calls`.

On `POST /runs` the API creates a `QUEUED` run and submits it to a bounded in-process `ThreadPoolExecutor` (`deal_intel/orchestration/executor.py`). That is plan change C1: the prototype does not have a durable job queue or a separate worker. The executor refuses new runs once today's `llm_calls` spend reaches `DAILY_COST_BUDGET_USD`. On startup, runs left queued or mid-stage are marked `FAILED` with `INTERRUPTED` so they can be resumed.

The runner (`deal_intel/orchestration/runner.py`) walks an explicit state machine:

`QUEUED → AUTHORIZING → (DENIED | RETRIEVING → ANALYZING → SYNTHESIZING → VALIDATING → (AWAITING_APPROVAL | COMPLETED))`

`FAILED` is reachable from any working state and is resumable. Stages persist output, an append-only event, and the new state in a short transaction. Model work happens outside that transaction.

Stage order: authorize, retrieve, deal snapshot, conversation intelligence and stakeholder map in parallel, negotiation strategy, policy, render-time guardrails, render.

Deterministic code owns permissions, retrieval filters, policy thresholds, validation, citations, state transitions, and rendering. Models extract and synthesise. Contracts are strict Pydantic models with `extra="forbid"`.

## Agents

| Piece | Role | Model | Tools |
|---|---|---|---|
| Deal snapshot | Copy opportunity, account, and in-scope pricing notes | none | none |
| Conversation intelligence | Goals, drivers, objections, conflicts, missing items | extraction (`MODEL_EXTRACTION`) | `search_evidence`, `get_evidence` |
| Stakeholder map | Buying committee, unmatched speakers, off-CRM people | extraction | none (single call) |
| Negotiation strategy | Summary, negotiation state, next actions | strategy (`MODEL_STRATEGY`, adaptive thinking, `STRATEGY_EFFORT`) | `search_evidence`, `get_evidence` |

Prompts are versioned files under `deal_intel/agents/prompts/<agent>/v1.md`. The harness (`deal_intel/agents/base.py`) builds a scoped pack, asserts every chunk is in scope before any model call, frames evidence as untrusted data, and retries on schema or guardrail feedback. Tools are bound to the run's `ScopedRetriever`; they can only narrow. Hidden and unknown ids return the same "not found" note. The tool loop is capped by `MAX_TOOL_CALLS`.

`PolicySummary` is passed to the strategy agent only when `scope.policies_allowed`. A failed subagent marks the run degraded and the run continues; strategy failure, `ScopeViolation`, or a token-budget overrun fails the run.

## Permissions and retrieval

`authorize()` validates identifiers, then decides allow or deny. Denied results carry a reason code and one external message: `You are not authorized to generate a brief for this request.` They never hold account names. Access levels are ordered `standard < restricted < sensitive_pricing`.

`ScopedRetriever` accepts only an `AccessScope`. One SQL predicate builder (mirrored in Python) intersects snapshot, account or policy, opportunity, source types, access level, and the sensitive-pricing flag. Search is Postgres full-text (`websearch_to_tsquery` + `ts_rank_cd`) times reliability and recency. Packs fill a per-agent token budget. Pricing visibility is `visible` or `none`; there is no `partial`.

Read-time checks apply to stored runs, briefs, and traces: the requester, or a user whose own scope covers the account at the brief's `max_access_level`. Unauthorised and unknown both return the same 404. Traces are redacted to ids and metrics for readers below the brief's level.

## Approvals, guardrails, rendering

The policy engine evaluates facts from next actions and permitted pricing notes against rules R1–R7. Approvals are keyed by subject and role. No eligible approver → status `escalated`; the run completes when no `pending` approvals remain. `decide()` checks eligibility, appends an event, re-renders, and updates the run.

Generation-time validators check citations, grounded numbers, quotes, names, approval-assertion wording, and customer-facing leaks. Render-time checks lint customer-facing language, check approval consistency, and scan canaries built from out-of-scope chunks. A canary hit stores a hash of the canary, never the value.

The renderer writes a typed `Brief` and Markdown with the nine section headings in order. Approval labels are generated by code. Replay writes a new version from stored stage outputs with no model calls.

## Observability and cost accounting

`PostgresTracer` writes `trace_spans`. Span attributes are a whitelist of short tokens; evidence text cannot be stored. `llm_calls` stores ids, token counts, cost, stop reason, and latency, not prompt or completion text. Logs are JSON with `run_id` and `span_id`, filtered for secret patterns.

Cost is computed from `MODEL_PRICES_USD_PER_MTOK` and the four token counts the provider reports (input, output, cache write, cache read). That is an accounting path, not a measured result. Cache hit rate, tokens per agent, and USD per brief from live runs are **TBD (measured in M7 live)**.

## Evaluation

`scripts/evaluate.py --from-fixtures` runs the four eval pairs with the fake client and prints citation validity, grounded-number rate, section completeness, approval routing accuracy, denial correctness, degraded rate, mean cost and tokens, and guardrail drops. Safety suites under `tests/safety/` cover injection, leakage, verbal approval (`SLK-1003-02` must not become an approved status), and a broken scope predicate that must raise `ScopeViolation` before any model call.

`--live` exists and requires `LIVE_LLM_TESTS=1`. Live columns, and any comparison of two-model routing against a single model at low effort, are **TBD (measured in M7 live)**.

## Failure handling

| Event | Behaviour |
|---|---|
| Unknown or unauthorised request | `DENIED` after authorize; no retrieval rows; one generic message |
| Empty evidence pack | Agent returns its empty output; zero model calls |
| Out-of-scope chunk in a pack | `ScopeViolation`; the run fails |
| Subagent failure | Run continues, degraded, with a warning |
| Strategy failure, budget overrun | Run `FAILED`; resume skips completed stages |
| Guardrail findings | Retry with feedback, then drop the item and revalidate; fail only if the executive summary cannot be salvaged |
| Process restart mid-run | Startup sweep marks the run `FAILED`/`INTERRUPTED`; resume is explicit |
| Daily spend at the budget | Executor refuses new runs until the next day |

## Production path

The prototype already has the state machine, scoped retrieval, typed contracts, policy and guardrails, persisted traces, versioned prompts, and a containerised Compose stack. What changes later is the substrate, not those contracts.

**Queue (C1, later).** Today's executor is an in-memory thread pool inside the API process. A restart loses the queue (interrupted runs are marked resumable). Production replaces that with a managed queue and worker processes, autoscaled on depth, so the API is not tied to long model calls and a single process is not the unit of concurrency.

Other production steps, in the order they would break first without them:

1. **Identity.** Replace the simulated `user_id` with SSO at the API boundary. The permission gate stays; it receives a verified id.
2. **Secrets.** Move the API key and database URL to a secrets manager with rotation. The prototype reads them from the environment.
3. **Model access.** Put a gateway in front of the SDK for per-team budgets, fallback, and a data-retention agreement. The client wrapper and price table stay.
4. **Embeddings and retrieval.** Hybrid search behind `EMBEDDINGS_ENABLED` (optional M8). Lexical FTS remains the prototype path.
5. **Prompt management.** Versioned files and hashes are already in the repo; production needs a release process and canary evaluation on model or prompt changes.
6. **Observability.** An OpenTelemetry exporter behind the existing `Tracer` protocol (optional M8). Postgres remains the audit source of truth.

Connectors (Salesforce, Gong, Slack), row-level security, notifications for approvers, and object storage for briefs are also production-only. See `docs/architecture.md` section 16 and `docs/security.md` section 5.

## Measured values

All rows are **TBD (measured in M7 live)**. Do not treat configured prices or the committed fixture baseline as live measurements.

| Metric | Value | Source once recorded |
|---|---|---|
| Citation validity | TBD (measured in M7 live) | `scripts/evaluate.py --live` |
| Grounded-number rate | TBD (measured in M7 live) | same |
| Section completeness | TBD (measured in M7 live) | same |
| Approval routing accuracy | TBD (measured in M7 live) | same |
| Denial correctness | TBD (measured in M7 live) | same |
| Degraded rate | TBD (measured in M7 live) | same |
| Mean cost USD / tokens per brief | TBD (measured in M7 live) | `llm_calls` over the four demo runs |
| Cost per brief, by agent | TBD (measured in M7 live) | same |
| Guardrail drops by check | TBD (measured in M7 live) | evaluation report |
| Run latency, end to end and per stage | TBD (measured in M7 live) | `trace_spans` |
| Tool calls per agent call | TBD (measured in M7 live) | traces |
| Cache hit rate | TBD (measured in M7 live) | `llm_calls` / output cache |
| Two-model routing vs single model at low effort | TBD (measured in M7 live) | comparison run |
| Safety suite results / canary hits | TBD (measured in M7 live) | `tests/safety/` against live fixtures |

`tests/fixtures/eval_baseline.json` is the committed regression baseline for `--from-fixtures`. It is not a live measurement.
