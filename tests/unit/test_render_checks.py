"""Render-time checks in isolation: the customer-facing lint and the leakage canary set."""

import hashlib

import pytest
from sqlalchemy.orm import Session

from deal_intel.contracts.guardrails import GuardrailCheck, GuardrailOutcome
from deal_intel.guardrails.canaries import (
    CanaryHit,
    CanaryKind,
    CanarySet,
    build_canary_set,
    find_leaks,
)
from deal_intel.guardrails.render_checks import WITHHELD_CUSTOMER_TEXT, lint_customer_facing
from deal_intel.permissions.gate import authorize
from deal_intel.retrieval.ingest import latest_snapshot_id

REF = "negotiation_strategy.next_actions[0]"
RULE_1_PHRASE = "Discounts greater than 10 percent require"


def test_plain_customer_text_passes_the_lint() -> None:
    lint = lint_customer_facing("Let us walk through the migration plan.", False, REF)

    assert (lint.text, lint.results) == ("Let us walk through the migration plan.", ())


@pytest.mark.parametrize("approved", [False, True])
def test_internal_workflow_terms_are_always_withheld(approved: bool) -> None:
    lint = lint_customer_facing("Deal Desk is reviewing the pricing note.", approved, REF)

    assert lint.text == WITHHELD_CUSTOMER_TEXT
    [result] = lint.results
    assert (result.check, result.outcome, result.item_ref) == (
        GuardrailCheck.CUSTOMER_FACING_LEAK,
        GuardrailOutcome.WARNING,
        REF,
    )


def test_concession_language_needs_an_approval() -> None:
    text = "We can offer a shorter term."

    unapproved = lint_customer_facing(text, False, REF)
    approved = lint_customer_facing(text, True, REF)

    assert unapproved.text == WITHHELD_CUSTOMER_TEXT
    assert [result.check for result in unapproved.results] == [GuardrailCheck.LANGUAGE_LINT]
    assert (approved.text, approved.results) == (text, ())


def canaries_for(session: Session, user_id: str, opportunity_id: str) -> CanarySet:
    access = authorize(session, user_id, opportunity_id)
    snapshot = latest_snapshot_id(session)
    assert snapshot is not None
    return build_canary_set(session, snapshot, access.scope, [user_id, opportunity_id])


def canary_values(canaries: CanarySet) -> set[str]:
    return {value for _, value in canaries.strings}


def test_canaries_come_from_what_the_narrow_reader_cannot_see(ingested_session: Session) -> None:
    canaries = canaries_for(ingested_session, "USR-5007", "OPP-1001")
    values = canary_values(canaries)

    assert {"PN-4001", "SLK-1001-01", "SLK-1001-02", "SLK-1001-03", RULE_1_PHRASE} <= values
    assert "ACC-2003" in values
    assert "Eclipse BioMaterials Ltd" in values
    assert "OPP-1001" not in values
    assert "Northstar Foods Cooperative" not in values
    assert "4217500" not in {figure.label() for figure in canaries.figures}


def test_canaries_for_a_denied_run_cover_every_account(ingested_session: Session) -> None:
    snapshot = latest_snapshot_id(ingested_session)
    assert snapshot is not None

    canaries = build_canary_set(ingested_session, snapshot, None, ["USR-5007", "OPP-1003"])

    values = canary_values(canaries)
    assert {"ACC-2001", "ACC-2003", "Eclipse BioMaterials Ltd", "PN-4004"} <= values
    assert "OPP-1003" not in values


def test_leak_scan_is_case_insensitive_and_reports_only_a_hash(ingested_session: Session) -> None:
    canaries = canaries_for(ingested_session, "USR-5007", "OPP-1001")

    hits = find_leaks(["the eclipse   biomaterials LTD renewal"], canaries)

    assert hits == [
        CanaryHit(
            kind=CanaryKind.NAME,
            canary_sha256=hashlib.sha256(b"Eclipse BioMaterials Ltd").hexdigest(),
        )
    ]


def test_hidden_figure_is_a_canary_unless_the_reader_can_see_it(
    ingested_session: Session,
) -> None:
    canaries = canaries_for(ingested_session, "USR-5007", "OPP-1001")

    assert find_leaks(["ACV is 4217500."], canaries) == []
    assert [hit.kind for hit in find_leaks(["Discount is 18%."], canaries)] == [CanaryKind.FIGURE]
    assert find_leaks(["Discount is 18%."], canaries.without_figures()) == []
