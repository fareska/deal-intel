# Strategic Deal Intelligence Assistant

The system takes an opportunity id and a requesting user id, retrieves only the evidence that user may see, runs three LLM agents over it, and produces a nine-section negotiation brief with citations. Recommendations that touch pricing, legal terms, customer-facing language, low confidence, or missing data are routed to human approvers. Every step is traced and persisted.

Interfaces: FastAPI service, server-rendered web UI (Jinja2 and HTMX), and a Typer CLI. Storage is Postgres 16. Models are Claude, reached through one client wrapper.

This is a local prototype over a synthetic dataset. Identity is a simulated `user_id`; there is no authentication. See `docs/security.md`.

## Prerequisites

- Docker and Docker Compose
- [uv](https://docs.astral.sh/uv/) (Python 3.12; the project requires `>=3.12,<3.13`)
- An Anthropic API key, only when you make live model calls

Install uv however you prefer. On macOS with Homebrew: `brew install uv`. Then:

```bash
uv python install 3.12
uv sync --group dev
```

`uv sync` installs the locked dependencies from `uv.lock`. Do not put the API key in a committed file.

## Setup

```bash
cp .env.example .env
# Export the key in the shell when you need live calls. Do not write it into .env.
export ANTHROPIC_API_KEY=...

make up
make migrate
make generate-slack
make ingest
```

`make up` builds the app image and starts Postgres and the API (`docker compose up -d --build --wait`). Ports are bound to localhost only: `127.0.0.1:8000` (app) and `127.0.0.1:5432` (Postgres).

`make migrate` runs Alembic to head inside the app container.

`make generate-slack` writes `synthetic_data/slack/account_team_updates.tsv` from the authored rows in `deal_intel/retrieval/slack_dataset.py`.

`make ingest` loads reference tables and evidence chunks in one transaction inside the app container.

The API is ready when `GET http://127.0.0.1:8000/readyz` returns 200. The UI is at [http://127.0.0.1:8000/ui](http://127.0.0.1:8000/ui). CLI commands that talk to the API:

```bash
uv run deal-intel generate --opp OPP-1001 --user USR-5001 --wait
```

Admin commands (`ingest`, `generate-slack`) run in-process and need `DATABASE_URL`. Run commands are a thin HTTP client and never import the database or orchestration modules.

`make down` stops the Compose stack. The Postgres volume is kept.

## Configuration

Environment variables match `docs/PLAN.md` section 7. Defaults below are the values in `deal_intel/config.py` and `.env.example`. Pydantic-settings reads `.env`; `ANTHROPIC_API_KEY` should stay in the shell or a secrets manager, never in a committed file.

| Variable | Default | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | none | Model access; set in the shell, never committed |
| `DATABASE_URL`, `TEST_DATABASE_URL` | local Compose URLs | Postgres (`deal_intel` and `deal_intel_test`) |
| `MODEL_STRATEGY` | `claude-opus-5-5` | Strategy-agent model |
| `MODEL_EXTRACTION` | `claude-haiku-4-5-20251001` | Extraction-agent model (conversation intelligence, stakeholder map) |
| `STRATEGY_EFFORT` | `high` | Strategy-agent reasoning effort (`low`, `medium`, `high`, `xhigh`, `max`) |
| `MAX_TOOL_CALLS` | `4` | Tool-loop bound per agent call |
| `RUN_INPUT_TOKEN_BUDGET` | `80000` | Hard cap per run |
| `DAILY_COST_BUDGET_USD` | `20` | Executor refuses new runs once today's `llm_calls` spend reaches this |
| `RUN_EXECUTOR_WORKERS` | `2` | Concurrent runs in the API process |
| `APPROVAL_EXPIRY_HOURS` | `168` | Pending approvals expire after this |
| `EMBEDDINGS_ENABLED` | `false` | Hybrid retrieval (optional M8; not wired) |
| `LLM_CLIENT` | `anthropic` | `fake` for tests and fixture replay |
| `RECORD_FIXTURES` | `0` | Write fixtures from live calls |
| `APP_ENV`, `LOG_LEVEL` | `dev`, `INFO` | Runtime |

Model ids were confirmed against the Anthropic models overview on 2026-09-26. Routing is by role: `strategy` uses `MODEL_STRATEGY` with adaptive thinking and `STRATEGY_EFFORT`; `extraction` uses `MODEL_EXTRACTION` with neither (`deal_intel/llm/routing.py`). Settings refuse to load if a routed model has no row in `MODEL_PRICES_USD_PER_MTOK`.

Configured prices (USD per million tokens, not measured spend):

| Model | Input | Output | Cache write (5 min) | Cache read |
|---|---|---|---|---|
| `claude-opus-5-5` | 4 | 20 | 5 | 0.20 |
| `claude-haiku-4-5-20251001` | 1 | 5 | 1.25 | 0.10 |

Further settings live in `deal_intel/config.py` (pack token budgets, policy thresholds, reliability weights, API timeouts, `eval_tolerance`). Compose overrides `DATABASE_URL` inside the app container so it points at the `postgres` service.

## Demo scenarios

Four scenarios. Commands that pass `--fresh` call the live model, bypass idempotency and the output cache, and **cost money**. Live last lines, token counts, and USD per brief are **TBD until M7 recording**.

Expected terminal behaviour below is from the design and the deterministic harness (`docs/PLAN.md` M4/M5), not from a recorded live run.

```bash
# 1. Standard late-stage renewal. Design: completes, or awaits a human_reviewer
#    approval for the SLK-1001-03 pilot-sequencing conflict.
uv run deal-intel generate --opp OPP-1001 --user USR-5001 --wait --fresh

# 2. Proof / expansion. Design: SLK-1002-02 conflict and an off-CRM site IT lead
#    from SLK-1002-01 on the stakeholder map.
uv run deal-intel generate --opp OPP-1002 --user USR-5002 --wait --fresh

# 3. Restricted opportunity. Design: AWAITING_APPROVAL; deal_desk pending and
#    eligible to USR-5005; sales_leader and legal escalated.
uv run deal-intel generate --opp OPP-1003 --user USR-5003 --wait --fresh
uv run deal-intel approvals list --user USR-5005
# Then, with ids from the list (live ids TBD):
# uv run deal-intel approvals decide <id> --user USR-5005 --approve --note "..."
# uv run deal-intel approvals decide <id> --user USR-5005 --reject --note "..."

# 4. Denial. Prints the generic message and exits 3.
uv run deal-intel generate --opp OPP-1003 --user USR-5007 --wait --fresh
```

A successful `--wait` prints the brief Markdown (including when the run is `AWAITING_APPROVAL`). A denial prints `You are not authorized to generate a brief for this request.` and exits 3. A failed run exits 4. An unreachable API exits 5. Omit `--fresh` to reuse an idempotent run when the evidence hash, prompt hashes, and model config match.

`--fresh` and `RECORD_FIXTURES=1` are the live path. Do not run them until you intend to spend tokens. Expected total for the four scenarios is under a few dollars once recorded; the measured total is **TBD**.

Without `--fresh`, and with `LLM_CLIENT=fake` plus recorded fixtures, the same commands replay from disk. Agent fixtures for the three authorised pairs are not recorded yet (`tests/fixtures/llm/` holds only harness test fixtures).

## UI walkthrough

Open [http://127.0.0.1:8000/ui](http://127.0.0.1:8000/ui). Every page has a **Viewing as** selector; the chosen `user_id` travels as a query parameter and is the simulated identity.

1. Set **Viewing as** to `USR-5001` (account owner). Open **New run**. Choose opportunity `OPP-1001`, requesting user `USR-5001`. Check **Fresh run (live model calls)** only when you intend a billed run (cost **TBD**). Submit. The status panel polls every two seconds until a terminal state, and also works with a manual refresh (no JavaScript required).
2. When the brief is ready, the page shows Confidence and Review Warnings near the top (highlighted when warnings or conflicts exist), cost and tokens by agent, version history with a Replay button, then the nine sections: Deal Snapshot, Executive Summary, Buyer Goals and Business Drivers, Stakeholder Map, Negotiation State, Recommended Next Actions, Missing Information, Source Evidence, and the warnings already shown. Snapshot figures are copied from Salesforce by code. Design expects the `SLK-1001-03` pilot-sequencing conflict in Confidence and Review Warnings, and the `SLK-1001-02` out-of-office context in Missing Information or Next Actions.
3. Open **Trace** from the status panel. Spans show kind, name, status, duration, tokens, and cost. Evidence appears as ids only.
4. Switch **Viewing as** to `USR-5003`. New run on `OPP-1003`, optionally fresh (live, cost **TBD**). Design: pending Deal Desk approval, escalated sales-leader and legal approvals, internal-only labels, and the "verbally okayed" Slack update (`SLK-1003-02`) as a conflict, not as an approval.
5. Switch **Viewing as** to `USR-5005` (Deal Desk). Open **Approvals**. Approve one pending item and reject another (note field optional). Return to the brief; version 2 should show the new labels and, when a customer-facing item was approved, templated customer-safe language.
6. Switch **Viewing as** to `USR-5007`. Request `OPP-1003`. The UI shows the generic not-found page for unknown and unauthorised reads alike. A generate as this user ends `DENIED` with the generic message and a short trace (no retrieval rows).

A 15-minute interview outline is in `docs/demo-script.md`.

## Tests

Postgres must be up (`make up`) and `.env` must set `TEST_DATABASE_URL`. The session fixture migrates the test database once and truncates between tests.

```bash
make check          # ruff lint + format check, then pytest
uv run pytest -q    # same tests; live-marked tests are deselected
```

`make check` is `ruff check .`, `ruff format --check .`, and `uv run pytest -q`. Pytest defaults to `-m 'not live'`. Live model tests require `LIVE_LLM_TESTS=1` and `LLM_CLIENT=anthropic`, and they spend money (cost **TBD**).

Safety and regression suites: `uv run pytest tests/safety tests/regression -q`.

## Evaluation

```bash
uv run python scripts/evaluate.py --from-fixtures
```

This runs the four eval pairs (`USR-5001/OPP-1001`, `USR-5002/OPP-1002`, `USR-5003/OPP-1003`, `USR-5007/OPP-1003`) against the test database with the fake client and prints the metrics table (citation validity, grounded-number rate, section completeness, approval routing, denial correctness, degraded rate, mean cost and tokens, guardrail drops). Until agent fixtures are recorded, the script notes that it is using stub agents.

`--from-artifacts DIR` reads brief JSON from an artifacts folder. `--live` requires `LIVE_LLM_TESTS=1` and the Anthropic client; measured live numbers are **TBD**. `--write-baseline` rewrites `tests/fixtures/eval_baseline.json` and is never automatic.

## Documentation

| Document | Content |
|---|---|
| `docs/architecture.md` | Built design, C1–C16, diagrams |
| `docs/technical-overview.md` | Architecture summary, production path, measured values |
| `docs/security.md` | Threat model and controls |
| `docs/deliverables.md` | Assignment item → file |
| `docs/demo-script.md` | 15-minute interview outline |
| `docs/diagrams/*.mmd` | Mermaid sources (SVG export pending) |
| `docs/PLAN.md` | Milestone plan (do not treat as the built system) |

Live run outputs will land under `artifacts/<date>/` after M7 recording. That folder is empty except for `.gitkeep`.
