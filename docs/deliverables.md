# Deliverables

The original home-task brief (`Cato_GTM_AI_Engineer_Home_Task.md`) is not in this repository. This map uses the M7 submission list in `docs/PLAN.md` and the T25 checklist in `docs/reference/sub.md` (assignment items, sections 3 to 10 of that brief). Paths point at files that exist; missing artifacts are marked pending.

## Submission items (PLAN M7 / T25)

| Item | File or folder | Status |
|---|---|---|
| README: setup, configuration, models, demo commands, UI, tests, evaluation | `README.md` | present |
| Architecture (reference design revised with PLAN section 2) | `docs/architecture.md` | present |
| Technical overview (cost, latency, evaluation, production path) | `docs/technical-overview.md` | present; measured numbers TBD (M7 live) |
| Security notes | `docs/security.md` | present; live safety counts TBD (M7 live) |
| Deliverables map | `docs/deliverables.md` | this file |
| Demo script / 15-minute outline | `docs/demo-script.md` | present |
| Logical diagram (Mermaid) | `docs/diagrams/logical.mmd` | present |
| Deployment diagram (Mermaid) | `docs/diagrams/deployment.mmd` | present |
| Permissions diagram (Mermaid) | `docs/diagrams/permissions.mmd` | present |
| Run-state diagram (Mermaid) | `docs/diagrams/run_states.mmd` | present |
| Diagram SVG (or PNG) exports | `docs/diagrams/*.svg` | pending |
| Slack-style account-team updates | `synthetic_data/slack/account_team_updates.tsv` | present |
| Slack dataset notes | `synthetic_data/README.md` | present |
| Slack golden labels | `tests/fixtures/slack_golden.json` | present |
| Evaluation script | `scripts/evaluate.py` | present |
| Evaluation metrics and report | `deal_intel/evaluation/metrics.py`, `report.py`, `runner.py` | present |
| Fixture-replay goldens | `tests/fixtures/golden/` | present (structure/citations; not live recordings) |
| Expected policy rules | `tests/fixtures/expected_rules.json` | present |
| Committed eval baseline | `tests/fixtures/eval_baseline.json` | present (fixture/stub baseline, not live) |
| Regression tests | `tests/regression/` | present |
| Safety tests (injection, leakage, verbal approval, scope assertion) | `tests/safety/` | present |
| Injection fixtures | `tests/fixtures/injection/` | present |
| Live demo runs (`--fresh`, `RECORD_FIXTURES=1`) | `artifacts/<date>/` | pending |
| Artifact exporter | `scripts/export_artifacts.py` | pending |
| Live briefs (Markdown and JSON, each version) | `artifacts/<date>/` | pending |
| Live traces and `llm_calls` summaries | `artifacts/<date>/` | pending |
| Approvals before and after `USR-5005` decisions | `artifacts/<date>/` | pending |
| Denied-run record (`USR-5007/OPP-1003`) | `artifacts/<date>/` | pending |
| Artifacts README (files, model settings, total cost, commit hash) | `artifacts/<date>/README.md` | pending |
| UI screenshots | `artifacts/<date>/` | pending |
| Recorded agent fixtures for the three authorised pairs | `tests/fixtures/llm/<agent>/` | pending (harness test fixtures only) |
| Link checker | `scripts/check_links.py` | pending |

`docs/PLAN.md` and `docs/reference/**` are planning and background. They are not submission deliverables.

## Four demo scenarios

| Scenario | Command / UI | Expected design outcome | Live artifact |
|---|---|---|---|
| `USR-5001` / `OPP-1001` | `deal-intel generate --opp OPP-1001 --user USR-5001 --wait --fresh` or UI new run | Brief with Salesforce snapshot figures, citations, `SLK-1001-03` conflict, `SLK-1001-02` context | pending |
| `USR-5002` / `OPP-1002` | `deal-intel generate --opp OPP-1002 --user USR-5002 --wait --fresh` | `SLK-1002-02` conflict; off-CRM site IT lead from `SLK-1002-01` | pending |
| `USR-5003` / `OPP-1003` then `USR-5005` decides | generate, then `approvals list` / `approvals decide` | `AWAITING_APPROVAL`; `deal_desk` pending; `sales_leader` and `legal` escalated; v2 labels after one approve and one reject | pending |
| `USR-5007` / `OPP-1003` | generate; CLI exit 3 | Generic denial; no retrieval; no restricted canaries | pending |

## Capability → code

These rows cover the implementation the assignment asks a reviewer to inspect. They already exist.

| Capability | Where it lives |
|---|---|
| Permission gate and access contracts | `deal_intel/permissions/gate.py`, `deal_intel/contracts/access.py` |
| Scoped retrieval and packs | `deal_intel/retrieval/retriever.py`, `packing.py`, `permissions/scope.py` |
| Evidence ingest and chunkers | `deal_intel/retrieval/ingest.py`, `chunkers/` |
| Slack dataset authoring | `deal_intel/retrieval/slack_dataset.py` |
| Deal snapshot (no LLM) | `deal_intel/agents/deal_snapshot.py` |
| Three agents and prompts | `deal_intel/agents/conversation_intelligence.py`, `stakeholder_map.py`, `negotiation_strategy.py`, `agents/prompts/` |
| Tool loop and LLM client | `deal_intel/llm/tool_loop.py`, `client.py`, `anthropic_client.py` |
| Run state machine and executor | `deal_intel/orchestration/runner.py`, `executor.py`, `stages.py` |
| Policy engine and eligibility | `deal_intel/policy/engine.py`, `rules.py`, `eligibility.py` |
| Guardrails, canaries, wording | `deal_intel/guardrails/` |
| Brief renderer and labels | `deal_intel/rendering/` |
| API, UI, CLI | `deal_intel/api/`, `deal_intel/ui/`, `deal_intel/cli.py` |
| Tracing and secret filter | `deal_intel/observability/` |
| Nine-section brief contract | `deal_intel/contracts/brief.py` |
| Compose / image | `docker-compose.yml`, `Dockerfile` |
| Configuration | `deal_intel/config.py`, `.env.example` |
