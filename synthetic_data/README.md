# Synthetic Deal Intelligence Exam Data

This folder contains fictional test data for the AI Engineer home task. The data is designed for building a Strategic Deal Intelligence Assistant that can retrieve source evidence, generate a negotiation-preparation brief, enforce permissions, route approval-sensitive recommendations, and explain uncertainty.

All accounts, people, emails, phone placeholders, commercial values, dates, and call content in this folder are synthetic. The data is inspired by common GTM systems and sales workflows, but it does not reuse real customer names, real contacts, real emails, real phone numbers, exact source values, or literal call notes from the reference artifacts.

The base exam asks candidates to generate a small synthetic Slack-style update dataset as part of the implementation and ingest it as an additional evidence source. This repository includes one sample generated dataset under `synthetic_data/slack/account_team_updates.tsv`.

The scenario timeline uses recent-past synthetic dates in March-June 2026. Generated updates, demo artifacts, and tests should preserve this chronology: calls happen first, account-team updates follow the relevant calls, action deadlines follow the evidence, and close dates come last.

TSV files use tabs between columns. List-valued fields inside a TSV cell use commas, for example `allowed_account_ids`, `allowed_source_types`, and Gong `participants`.

## Files

```text
synthetic_data/
  README.md
  salesforce/
    accounts.tsv
    opportunities.tsv
    contacts.tsv
  gong/
    gong_call_summaries.tsv
    transcripts/
      OPP-1001_CALL-001.md
      OPP-1001_CALL-004.md
      OPP-1001_CALL-008.md
      OPP-1002_CALL-010.md
      OPP-1002_CALL-014.md
      OPP-1002_CALL-018.md
      OPP-1003_CALL-019.md
      OPP-1003_CALL-023.md
      OPP-1003_CALL-027.md
  policies/
    access_permissions.tsv
    deal_desk_policy.md
  pricing/
    pricing_notes.tsv
  slack/
    account_team_updates.tsv
```

## Scenario Overview

The dataset contains three fictional strategic opportunities:

- `OPP-1001`: a standard but complex late-stage enterprise renewal.
- `OPP-1002`: a technical proof and expansion opportunity with product validation friction.
- `OPP-1003`: a restricted high-risk opportunity with approval-sensitive commercial and legal constraints.

Use `opportunity_id`, `account_id`, `contact_id`, and `call_id` to join data across files.

Access-control fixtures include account owners for the standard demo opportunities. `USR-5001` owns `OPP-1001`, `USR-5002` owns `OPP-1002`, `USR-5003` owns restricted `OPP-1003`, and `USR-5007` is intentionally insufficiently privileged for the restricted-account denial scenario.

## Candidate Expectations

The data supports generation of a structured brief with:

- Deal Snapshot
- Executive Summary
- Buyer Goals and Business Drivers
- Stakeholder Map
- Negotiation State
- Recommended Next Actions
- Missing Information
- Source Evidence
- Confidence and Review Warnings

The dataset intentionally includes missing information, conflicting signals, restricted sources, and human-approval triggers. Source sensitivity is also intentionally split across files: Gong and generated Slack rows carry `source_access_level`, while pricing notes must be interpreted together with the opportunity's restricted status and the Deal Desk policy.

## Candidate-Generated Slack-Style Updates

This repository includes candidate-generated synthetic Slack-style account-team updates at:

```text
synthetic_data/slack/account_team_updates.tsv
```

The file is tab-separated and contains:

- `update_id`
- `opportunity_id`
- `account_id`
- `update_date`
- `channel`
- `author_role`
- `synthetic_notice`
- `source_access_level`
- `update_text`

Each row is clearly marked synthetic. The updates add account-team context, reinforce known facts, and introduce ambiguity or possible conflicts that the prototype must surface in the brief.

### How it is produced

The nine rows (three per opportunity) are written by hand in `deal_intel/retrieval/slack_dataset.py` and frozen there, so running the generator again produces the same file byte for byte. No LLM is involved. To write the file, run:

```bash
deal-intel generate-slack --path synthetic_data
```

Before writing, the command checks every row against the other files in this folder and refuses to write if any check fails:

- Each update is dated after the opportunity's latest Gong call and before its close date.
- The account and update id match the opportunity.
- `source_access_level` is never below the opportunity's baseline, so rows about a restricted account are at least `restricted`.
- Any row that mentions a discount, reduction, concession, or percentage is `sensitive_pricing`.
- Every row carries the standard synthetic notice and contains no email addresses, phone-like numbers, tabs, or line breaks.

The expected effect of each update on the brief (reinforces, adds context, or conflicts) is recorded in `tests/fixtures/slack_golden.json`.
