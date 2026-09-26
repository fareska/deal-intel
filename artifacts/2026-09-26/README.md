# Artifacts 2026-09-26

Commit: `7a4703f7f9a2605d5ebf2215d383796d26829f8e`

## Model settings

| Variable | Value |
|---|---|
| `MODEL_STRATEGY` | claude-sonnet-4-6 |
| `MODEL_EXTRACTION` | claude-haiku-4-5-20251001 |
| `STRATEGY_EFFORT` | medium |

Total cost: $1.942817

## Files

| File | Description |
|---|---|
| `USR-5001_OPP-1001/brief.v1.md` | Brief Markdown |
| `USR-5001_OPP-1001/brief.v1.json` | Brief JSON |
| `USR-5001_OPP-1001/trace.json` | Trace spans, redacted as the reader |
| `USR-5001_OPP-1001/llm_calls.json` | LLM call tokens and cost (no prompts) |
| `USR-5001_OPP-1001/approvals.before.json` | Approval requests before decisions |
| `USR-5001_OPP-1001/approvals.after.json` | Approval requests and events after decisions |
| `USR-5002_OPP-1002/brief.v1.md` | Brief Markdown |
| `USR-5002_OPP-1002/brief.v1.json` | Brief JSON |
| `USR-5002_OPP-1002/trace.json` | Trace spans, redacted as the reader |
| `USR-5002_OPP-1002/llm_calls.json` | LLM call tokens and cost (no prompts) |
| `USR-5002_OPP-1002/approvals.before.json` | Approval requests before decisions |
| `USR-5002_OPP-1002/approvals.after.json` | Approval requests and events after decisions |
| `USR-5003_OPP-1003/brief.v1.md` | Brief Markdown |
| `USR-5003_OPP-1003/brief.v1.json` | Brief JSON |
| `USR-5003_OPP-1003/brief.v2.md` | Brief Markdown |
| `USR-5003_OPP-1003/brief.v2.json` | Brief JSON |
| `USR-5003_OPP-1003/brief.v3.md` | Brief Markdown |
| `USR-5003_OPP-1003/brief.v3.json` | Brief JSON |
| `USR-5003_OPP-1003/trace.json` | Trace spans, redacted as the reader |
| `USR-5003_OPP-1003/llm_calls.json` | LLM call tokens and cost (no prompts) |
| `USR-5003_OPP-1003/approvals.before.json` | Approval requests before decisions |
| `USR-5003_OPP-1003/approvals.after.json` | Approval requests and events after decisions |
| `USR-5007_OPP-1003/run.json` | Denied run record as the requesting user |
| `USR-5007_OPP-1003/trace.json` | Trace spans, redacted as the reader |
| `account_team_updates.tsv` | Synthetic Slack account-team updates |
| `slack_golden.json` | Golden labels for Slack updates |

## Notes

- `USR-5001_OPP-1001` is the live `--fresh` run `3c49a101ee5d482aaaabe71217de9951` (`AWAITING_APPROVAL`, `$0.98`).
- `USR-5002_OPP-1002` is a fixture/cache replay (`$0`).
- `USR-5003_OPP-1003` is `022534328b1c4a07b4a250b17252816c` (`$0.96`). Deal Desk approved `PN-4004` and rejected `PN-4005`; `human_reviewer` is still pending so the run stays `AWAITING_APPROVAL`. `brief.v2` / `v3` are the post-decision renders.
- `USR-5007_OPP-1003` is the generic denial (no brief, two-span authorize trace).
- Re-running the exporter overwrites this README.

## Screenshots

Captured 2026-09-26.

| File | Section |
|---|---|
| `screenshots/01-new-run.png` | New-run form as `USR-5001` |
| `screenshots/02-brief-opp1001.png` | Run status + confidence / review warnings (hero) |
| `screenshots/02a-status-warnings.png` | Same as hero |
| `screenshots/02b-deal-snapshot.png` | Deal Snapshot |
| `screenshots/02c-executive-summary.png` | Executive Summary |
| `screenshots/02d-buyer-goals.png` | Buyer Goals and Business Drivers |
| `screenshots/02e-stakeholder-map.png` | Stakeholder Map |
| `screenshots/02f-negotiation-state.png` | Negotiation State |
| `screenshots/02g-next-actions.png` | Recommended Next Actions |
| `screenshots/02h-missing-information.png` | Missing Information |
| `screenshots/02i-cost-tokens.png` | Cost, tokens, version history |
| `screenshots/03-trace-opp1001.png` | Trace page top (hero) |
| `screenshots/03a-trace-top.png` | Same as hero: run + retrieve packs |
| `screenshots/03b-trace-retrieval-agents.png` | Snapshot + subagent stages |
| `screenshots/03c-trace-strategy-mid.png` | Strategy tool loop (middle) |
| `screenshots/03d-trace-strategy-end.png` | Strategy finish + policy/render |
| `screenshots/04-approvals-pending.png` | `USR-5005` pending approval |
| `screenshots/05-brief-v2-labels.png` | `OPP-1003` next actions: `[APPROVED]`, `[ESCALATED]`, `[PENDING]` |
| `screenshots/06-denied-not-found.png` | `USR-5007` denied `OPP-1003` (generic message) |
