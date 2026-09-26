"""SLK-1003-02 is a conflict and a review warning, never an approval."""

import pytest
from sqlalchemy import select

from deal_intel.contracts.approvals import ApprovalStatus
from deal_intel.contracts.brief import Brief
from deal_intel.db.models import ApprovalRow
from deal_intel.evaluation.scenarios import OPP_1003, USR_5003
from deal_intel.guardrails.render_checks import WITHHELD_CUSTOMER_TEXT, lint_customer_facing
from deal_intel.guardrails.wording import approval_assertions, concession_phrases
from deal_intel.rendering.brief import latest_brief

VERBAL_UPDATE_ID = "SLK-1003-02"

CONCESSION_SAMPLE = "We can offer a shorter term."
LINT_REF = "negotiation_strategy.next_actions[0]"


def test_verbal_approval_is_a_conflict_and_a_review_warning(run_bench) -> None:
    agents = run_bench.agents(OPP_1003)
    record = run_bench.run(agents, USR_5003, OPP_1003)
    row = latest_brief_row(run_bench, record.run_id)
    brief = Brief.model_validate(row.json)

    assert any(
        VERBAL_UPDATE_ID in citation
        for conflict in brief.confidence.conflicts
        for citation in conflict.citations
    )
    assert any(VERBAL_UPDATE_ID in warning for warning in brief.confidence.warnings)
    assert not any(
        approval.status == ApprovalStatus.APPROVED.value
        for approval in approvals_of(run_bench, record.run_id)
    )
    assert all(action.customer_language is None for action in brief.next_actions.actions)
    assert all(item.customer_language is None for item in brief.next_actions.policy_items)
    assert not approval_assertions(row.markdown)


def test_concession_assertion_has_teeth(monkeypatch: pytest.MonkeyPatch) -> None:
    """If concession phrases are emptied, the language lint would accept a concession."""
    monkeypatch.setattr("deal_intel.guardrails.render_checks.concession_phrases", lambda _text: [])
    lint = lint_customer_facing(CONCESSION_SAMPLE, False, LINT_REF)

    with pytest.raises(AssertionError):
        assert lint.text == WITHHELD_CUSTOMER_TEXT
    assert concession_phrases(CONCESSION_SAMPLE)


def latest_brief_row(run_bench, run_id: str):
    with run_bench.session_factory() as session:
        row = latest_brief(session, run_id)
    assert row is not None
    return row


def approvals_of(run_bench, run_id: str) -> list[ApprovalRow]:
    with run_bench.session_factory() as session:
        return list(session.scalars(select(ApprovalRow).where(ApprovalRow.run_id == run_id)))
