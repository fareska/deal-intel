# Deliverables

The assignment brief lives outside this repo (`Cato_GTM_AI_Engineer_Home_Task.md` in the exam data repo). This map uses the M7 list in `docs/PLAN.md` and T25 in `docs/reference/sub.md` (assignment sections 3 to 10). Status is “present” when the file is in git; “pending” only when it is still missing.

## Submission items (PLAN M7 / T25)

| Item | File or folder | Status |
|---|---|---|
| README: setup, configuration, models, demo commands, UI, tests, evaluation | `README.md` | present |
| Architecture (reference design revised with PLAN section 2) | `docs/architecture.md` | present |
| Technical overview (cost, latency, evaluation, production path) | `docs/technical-overview.md` | present; measured from fixtures + `artifacts/2026-09-26/` |
| Security notes | `docs/security.md` | present; safety suite 22 passed / 0 canary hits |
| Deliverables map | `docs/deliverables.md` | this file |
| Demo script / 15-minute outline | `docs/demo-script.md` | present |
| Logical diagram (Mermaid) | `docs/diagrams/logical.mmd` | present |
| Deployment diagram (Mermaid) | `docs/diagrams/deployment.mmd` | present |
| Permissions diagram (Mermaid) | `docs/diagrams/permissions.mmd` | present |
| Run-state diagram (Mermaid) | `docs/diagrams/run_states.mmd` | present |
| Diagram SVG (or PNG) exports | `docs/diagrams/*.svg` | present (`logical`, `deployment`, `permissions`, `run_states`) |
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
| Live agent recordings (`RECORD_FIXTURES=1`) | `tests/fixtures/llm/<agent>/` | present (three authorised pairs plus harness fixtures) |
| Artifact exporter | `scripts/export_artifacts.py` | present; writes `artifacts/<date>/` from existing run ids |
| Exported live briefs, traces, `llm_calls`, approvals, denial record | `artifacts/2026-09-26/` | present (`USR-5002` is cache replay; see that README) |
| Artifacts README (files, model settings, total cost, commit hash) | `artifacts/2026-09-26/README.md` | present; total **$1.942817** |
| UI screenshots | `artifacts/2026-09-26/screenshots/` | present (new run, brief, trace, approvals, v2 labels, denial) |
| Link checker | `scripts/check_links.py` | pending (T25 nicety; not required to demo) |

`docs/PLAN.md` and `docs/reference/**` are planning and background. They are not submission deliverables.

## Four demo scenarios

| Scenario | Command / UI | Expected design outcome | Agent fixtures | Exported folder |
|---|---|---|---|---|
| `USR-5001` / `OPP-1001` | `deal-intel generate --opp OPP-1001 --user USR-5001 --wait` or UI new run | Brief with Salesforce snapshot figures, citations, `SLK-1001-03` conflict, `SLK-1001-02` context | present | `artifacts/2026-09-26/USR-5001_OPP-1001/` (live $0.98) |
| `USR-5002` / `OPP-1002` | `deal-intel generate --opp OPP-1002 --user USR-5002 --wait` | `SLK-1002-02` conflict; off-CRM site IT lead from `SLK-1002-01` | present | `artifacts/2026-09-26/USR-5002_OPP-1002/` (cache replay $0) |
| `USR-5003` / `OPP-1003` then `USR-5005` decides | generate, then `approvals list` / `approvals decide` | `AWAITING_APPROVAL`; `deal_desk` pending; `sales_leader` and `legal` escalated; v2 labels after one approve and one reject | present | `artifacts/2026-09-26/USR-5003_OPP-1003/` (live $0.96, v1–v3) |
| `USR-5007` / `OPP-1003` | generate; CLI exit 3 | Generic denial; no retrieval; no restricted canaries | not an agent pair | `artifacts/2026-09-26/USR-5007_OPP-1003/` |

`--fresh` forces live model calls and skips the output cache. Replay without `--fresh` uses the recorded fixtures when `LLM_CLIENT=fake`.

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
