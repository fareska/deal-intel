# Demo script (15 minutes)

Interview outline from `docs/PLAN.md` section 8. Walk the four scenarios: `USR-5001/OPP-1001`, `USR-5003/OPP-1003` with a `USR-5005` decision, `USR-5007/OPP-1003` denied, and `USR-5002/OPP-1002` on the CLI.

Live `--fresh` runs and their costs are **TBD until M7 recording**. For the interview, start `--fresh` on the first scenario so the model is visible, and keep the other three pre-started (or replayed) if latency is a problem. The strategy call can run for minutes at `STRATEGY_EFFORT=high`. While it runs, stay on the trace page.

UI base: [http://127.0.0.1:8000/ui](http://127.0.0.1:8000/ui). Identity is the **Viewing as** selector.

| Clock | Step |
|---|---|
| 0:00–1:00 | Setup: Compose is up, data ingested, browser on `/ui`. One sentence on what the system does: scoped retrieval, three agents, cited brief, human approvals. |
| 1:00–4:00 | Scenario 1 (UI) |
| 4:00–6:00 | Trace for that run |
| 6:00–9:00 | Scenario 2 (UI): restricted deal and approvals |
| 9:00–11:00 | Scenario 3 (UI): generic denial |
| 11:00–13:00 | Scenario 4 (CLI) |
| 13:00–15:00 | Evaluation, cost, production path |

## 1. UI as `USR-5001`: `OPP-1001` (about 3 minutes)

Set **Viewing as** to `USR-5001`. **New run** → opportunity `OPP-1001`, requesting user `USR-5001`. Check **Fresh run (live model calls)** if this is the billed run (cost **TBD**). Submit.

The status panel polls until a terminal state. Open the brief.

Walk:

- Deal Snapshot: figures copied from Salesforce by code, not transcribed by a model. Pricing visibility `visible` or `none`.
- Citations on claim lines; Source Evidence lists each cited chunk once.
- Confidence and Review Warnings (highlighted near the top): design expects the `SLK-1001-03` pilot-sequencing conflict (Pavel Stone vs the March pilot-first plan).
- Missing Information or Next Actions: design expects the `SLK-1001-02` out-of-office context (Iris Calder 05-04 to 05-08; finance approval of the payment schedule).

CLI equivalent (live, cost **TBD**):

```bash
uv run deal-intel generate --opp OPP-1001 --user USR-5001 --wait --fresh
```

## 2. Trace page (about 2 minutes)

From the run status panel, open **Trace**.

Show: agent calls, tool calls, retrievals, guardrail results, tokens, and cost. Evidence is ids only. Span kinds include `run`, `stage`, `agent_call`, `llm_request`, `retrieval`, `tool`, and `guardrail`.

If the fresh run is still going, narrate the tree as spans appear instead of waiting in silence.

## 3. UI as `USR-5003`: `OPP-1003`, then `USR-5005` (about 3 minutes)

Switch **Viewing as** to `USR-5003`. New run on `OPP-1003` (fresh only if you intend another billed call; cost **TBD**).

Walk:

- Run ends `AWAITING_APPROVAL` (design).
- Recommended Next Actions: pending Deal Desk label, internal-only language on pricing.
- Escalated sales-leader and legal approvals (no eligible approver in the provided permission data).
- Confidence and Review Warnings: `SLK-1003-02` ("verbally okayed") is a conflict, never an approved status or approved-language rendering.

Switch **Viewing as** to `USR-5005`. Open **Approvals**. The pending Deal Desk item is eligible to this user. Approve one item and reject another (note optional). HTMX swaps the row; the form also works without JavaScript.

Return to the brief. Version 2 should show `[APPROVED by USR-5005 on <date>]` and `[REJECTED]`, and templated customer-safe language where an approved customer-facing item exists.

CLI equivalent (live, cost **TBD**):

```bash
uv run deal-intel generate --opp OPP-1003 --user USR-5003 --wait --fresh
uv run deal-intel approvals list --user USR-5005
uv run deal-intel approvals decide <approval_id> --user USR-5005 --approve --note "ok"
uv run deal-intel approvals decide <approval_id> --user USR-5005 --reject --note "no"
```

## 4. UI as `USR-5007`: denied `OPP-1003` (about 2 minutes)

Switch **Viewing as** to `USR-5007`. Request `OPP-1003` (new run or a direct brief URL).

Show the generic not-found page. Unknown and unauthorised reads use the same copy; the page must not name Eclipse, BioMaterials, or `ACC-2003`.

A generate as this user ends `DENIED` with `You are not authorized to generate a brief for this request.` The denied run's trace is two spans (authorize, done); there are no retrieval rows. Mention that `tests/safety/test_leakage.py` scans this denial, the brief surfaces, API bodies, and spans for out-of-scope canaries. Live canary counts are **TBD (measured in M7 live)**.

CLI equivalent (exit 3):

```bash
uv run deal-intel generate --opp OPP-1003 --user USR-5007 --wait --fresh
```

## 5. CLI: `USR-5002` / `OPP-1002` (about 2 minutes)

```bash
uv run deal-intel generate --opp OPP-1002 --user USR-5002 --wait --fresh
```

Live, cost **TBD**. Show:

- Conflict citing `slack:SLK-1002-02` against Gong or Salesforce (proof closeout is not closed).
- Stakeholder map: off-CRM site IT lead from `SLK-1002-01` (not in `contacts.tsv`).

Optional: `uv run deal-intel runs trace <run_id> --user USR-5002`.

## 6. Close (about 2 minutes)

- `uv run python scripts/evaluate.py --from-fixtures` — print the metrics table. Say that `--live` and the measured cost per brief are **TBD (measured in M7 live)** until fixtures are recorded.
- Configured routing: extraction on `claude-haiku-4-5-20251001`, strategy on `claude-opus-5-5` at `STRATEGY_EFFORT=high`. Whether the Opus-class model is worth it is an M7 measurement, not a claim today.
- What breaks first in production: the in-process executor (C1; a managed queue and workers come later), simulated identity, static TSV permissions and evidence, secrets in the environment, and unbounded spend without a model gateway. Details in `docs/technical-overview.md`.

## If the UI is unavailable

Every scenario has a CLI path above. The CLI covers the interview if the browser path slips.
