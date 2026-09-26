"""Policy rules and approval routing: rules over facts, then real runs over the dataset's
pricing notes and permissions."""

from dataclasses import replace
from decimal import Decimal

import pytest
from sqlalchemy import select

from deal_intel.config import get_settings
from deal_intel.contracts.agents.common import AgentName
from deal_intel.contracts.agents.conversation_intelligence import Conflict
from deal_intel.contracts.agents.negotiation_strategy import ProposedValues, SensitivityTag
from deal_intel.contracts.approvals import (
    ApprovalStatus,
    ApproverRole,
    PolicyOutput,
    RuleId,
    SubjectKind,
)
from deal_intel.contracts.guardrails import Confidence
from deal_intel.contracts.runs import RunState, StageName
from deal_intel.db.models import ApprovalRow, StageOutputRow
from deal_intel.policy.engine import evaluate
from deal_intel.policy.facts import RecommendationFacts
from deal_intel.policy.rules import Thresholds, fired_rules

NS = AgentName.NEGOTIATION_STRATEGY
CI = AgentName.CONVERSATION_INTELLIGENCE
HIGH_DISCOUNT_NOTE = "pricing:PN-4004"
ALTERNATIVE_NOTE = "pricing:PN-4005"
DEAL_DESK_APPROVER = "USR-5005"
PILOT_SEQUENCING_SLACK = "slack:SLK-1001-03"
PILOT_SEQUENCING_CALL = "gong_summary:CALL-002"
THRESHOLDS = Thresholds.from_settings(get_settings())
PLAIN_FACTS = RecommendationFacts(
    kind=SubjectKind.ACTION,
    recommendation_id="action:A1",
    subject_id="action:A1",
    summary="Confirm the schedule.",
    discount_pct=None,
    uplift_pct=None,
    liability_cap_change=False,
    tags=frozenset(),
    customer_facing=False,
    low_confidence=False,
    has_conflict=False,
    missing_source_data=False,
    proposed_values={},
    evidence_ids=("sfdc_opp:OPP-1001",),
)


def rule_ids(facts: RecommendationFacts) -> list[RuleId]:
    return [rule.id for rule in fired_rules(facts, THRESHOLDS)]


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({}, []),
        ({"discount_pct": Decimal(10)}, []),
        ({"discount_pct": Decimal(12)}, [RuleId.R1]),
        ({"discount_pct": Decimal(16)}, [RuleId.R1, RuleId.R2]),
        ({"uplift_pct": Decimal(0)}, []),
        ({"uplift_pct": Decimal(-1)}, [RuleId.R3]),
        ({"liability_cap_change": True}, [RuleId.R4]),
        ({"tags": frozenset({SensitivityTag.LEGAL_TERMS})}, [RuleId.R5]),
        ({"tags": frozenset({SensitivityTag.DATA_RETENTION})}, [RuleId.R5]),
        ({"customer_facing": True}, []),
        ({"customer_facing": True, "tags": frozenset({SensitivityTag.DISCOUNT})}, [RuleId.R6]),
        ({"low_confidence": True}, [RuleId.R7]),
        ({"has_conflict": True}, [RuleId.R7]),
        ({"missing_source_data": True}, [RuleId.R7]),
    ],
)
def test_rules_fire_on_their_facts(changes: dict, expected: list[RuleId]) -> None:
    assert rule_ids(replace(PLAIN_FACTS, **changes)) == expected


def test_thresholds_come_from_settings() -> None:
    settings = get_settings().model_copy(
        update={"policy_discount_deal_desk_threshold_pct": Decimal(20)}
    )
    facts = replace(PLAIN_FACTS, discount_pct=Decimal(18))

    assert [rule.id for rule in fired_rules(facts, Thresholds.from_settings(settings))] == [
        RuleId.R2
    ]


def test_approvals_are_keyed_by_subject_and_role() -> None:
    note = replace(
        PLAIN_FACTS,
        kind=SubjectKind.PRICING,
        recommendation_id=HIGH_DISCOUNT_NOTE,
        subject_id=HIGH_DISCOUNT_NOTE,
        discount_pct=Decimal(18),
        uplift_pct=Decimal(-8),
    )
    action = replace(PLAIN_FACTS, subject_id=HIGH_DISCOUNT_NOTE, discount_pct=Decimal(18))

    evaluation = evaluate([note, action], THRESHOLDS)

    assert [
        (request.subject_id, request.required_role, request.rule_ids, request.recommendation_ids)
        for request in evaluation.requests
    ] == [
        (
            HIGH_DISCOUNT_NOTE,
            ApproverRole.DEAL_DESK,
            [RuleId.R1, RuleId.R2, RuleId.R3],
            [HIGH_DISCOUNT_NOTE, "action:A1"],
        ),
        (
            HIGH_DISCOUNT_NOTE,
            ApproverRole.SALES_LEADER,
            [RuleId.R2],
            [HIGH_DISCOUNT_NOTE, "action:A1"],
        ),
    ]


def approvals_by_subject(run_bench, run_id: str) -> list[tuple[str, str, list[str], str]]:
    with run_bench.session_factory() as session:
        statement = (
            select(ApprovalRow)
            .where(ApprovalRow.run_id == run_id)
            .order_by(ApprovalRow.subject_id, ApprovalRow.required_role)
        )
        return [
            (row.subject_id, row.required_role, row.rule_ids, row.status)
            for row in session.scalars(statement)
        ]


def stored_policy(run_bench, run_id: str) -> PolicyOutput:
    with run_bench.session_factory() as session:
        row = session.scalars(
            select(StageOutputRow).where(
                StageOutputRow.run_id == run_id, StageOutputRow.stage == StageName.POLICY
            )
        ).one()
        return PolicyOutput.model_validate(row.output_json)


def without_pricing_actions(agents) -> None:
    strategy = agents.outputs[NS]
    kept = [action for action in strategy.next_actions if action.proposed_values is None]
    agents.outputs[NS] = strategy.model_copy(update={"next_actions": kept})


PN_4004_ROWS = [
    (
        HIGH_DISCOUNT_NOTE,
        ApproverRole.DEAL_DESK.value,
        [RuleId.R1.value, RuleId.R2.value, RuleId.R3.value],
        ApprovalStatus.PENDING.value,
    ),
    (
        HIGH_DISCOUNT_NOTE,
        ApproverRole.SALES_LEADER.value,
        [RuleId.R2.value],
        ApprovalStatus.ESCALATED.value,
    ),
]
PN_4005_ROW = (
    ALTERNATIVE_NOTE,
    ApproverRole.DEAL_DESK.value,
    [RuleId.R1.value, RuleId.R3.value],
    ApprovalStatus.PENDING.value,
)


def test_pricing_note_routes_approvals_when_no_action_mentions_it(run_bench) -> None:
    agents = run_bench.agents("OPP-1003")
    without_pricing_actions(agents)
    cited = {chunk for action in agents.outputs[NS].next_actions for chunk in action.evidence_ids}
    assert HIGH_DISCOUNT_NOTE not in cited

    record = run_bench.run(agents, "USR-5003", "OPP-1003")

    assert record.state is RunState.AWAITING_APPROVAL
    assert stored_policy(run_bench, record.run_id).fired_rules[HIGH_DISCOUNT_NOTE] == [
        RuleId.R1,
        RuleId.R2,
        RuleId.R3,
    ]
    assert approvals_by_subject(run_bench, record.run_id) == [*PN_4004_ROWS, PN_4005_ROW]
    brief = run_bench.briefs(record.run_id)[-1].json
    assert [item["subject_id"] for item in brief["next_actions"]["policy_items"]] == [
        HIGH_DISCOUNT_NOTE,
        ALTERNATIVE_NOTE,
    ]


def test_action_citing_a_note_with_its_discount_shares_the_note_approval(run_bench) -> None:
    agents = run_bench.agents("OPP-1003")

    record = run_bench.run(agents, "USR-5003", "OPP-1003")

    assert approvals_by_subject(run_bench, record.run_id) == [*PN_4004_ROWS, PN_4005_ROW]
    with run_bench.session_factory() as session:
        shared = session.scalars(
            select(ApprovalRow).where(
                ApprovalRow.subject_id == HIGH_DISCOUNT_NOTE,
                ApprovalRow.required_role == ApproverRole.DEAL_DESK,
            )
        ).one()
    assert shared.recommendation_ids == [HIGH_DISCOUNT_NOTE, "action:A1"]
    assert shared.eligible_user_ids == [DEAL_DESK_APPROVER]
    brief = run_bench.briefs(record.run_id)[-1].json
    [action] = [line for line in brief["next_actions"]["actions"] if line["id"] == "A1"]
    assert [label["kind"] for label in action["labels"]] == ["pending", "escalated"]
    assert [item["subject_id"] for item in brief["next_actions"]["policy_items"]] == [
        ALTERNATIVE_NOTE
    ]


def test_action_with_a_different_discount_gets_its_own_approval(run_bench) -> None:
    agents = run_bench.agents("OPP-1003")
    strategy = agents.outputs[NS]
    [pricing, other] = strategy.next_actions
    changed = pricing.model_copy(update={"proposed_values": ProposedValues(discount_pct=16)})
    agents.outputs[NS] = strategy.model_copy(update={"next_actions": [changed, other]})

    record = run_bench.run(agents, "USR-5003", "OPP-1003")

    subjects = {row[0] for row in approvals_by_subject(run_bench, record.run_id)}
    assert subjects == {"action:A1", HIGH_DISCOUNT_NOTE, ALTERNATIVE_NOTE}


def test_legal_approval_with_no_eligible_approver_is_escalated(run_bench) -> None:
    agents = run_bench.agents("OPP-1003")
    strategy = agents.outputs[NS]
    [pricing, other] = strategy.next_actions
    legal = other.model_copy(
        update={
            "sensitivity_tags": [SensitivityTag.LEGAL_TERMS],
            "proposed_values": ProposedValues(liability_cap_change="Cap at fees paid."),
        }
    )
    agents.outputs[NS] = strategy.model_copy(update={"next_actions": [pricing, legal]})

    record = run_bench.run(agents, "USR-5003", "OPP-1003")

    rows = approvals_by_subject(run_bench, record.run_id)
    assert record.state is RunState.AWAITING_APPROVAL
    assert rows == [
        (
            "action:A2",
            ApproverRole.LEGAL.value,
            [RuleId.R4.value, RuleId.R5.value],
            ApprovalStatus.ESCALATED.value,
        ),
        *PN_4004_ROWS,
        PN_4005_ROW,
    ]
    with run_bench.session_factory() as session:
        deal_desk = session.scalars(
            select(ApprovalRow).where(
                ApprovalRow.run_id == record.run_id,
                ApprovalRow.required_role == ApproverRole.DEAL_DESK,
                ApprovalRow.subject_id == HIGH_DISCOUNT_NOTE,
            )
        ).one()
    assert deal_desk.eligible_user_ids == [DEAL_DESK_APPROVER]


def test_pilot_sequencing_conflict_awaits_human_review(run_bench) -> None:
    agents = run_bench.agents("OPP-1001")
    findings = agents.outputs[CI]
    agents.outputs[CI] = findings.model_copy(
        update={
            "conflicts": [
                Conflict(
                    topic="Pilot site sequencing",
                    claim_a="Legacy-appliance sites should migrate before the pilot sites.",
                    claim_b="The March planning session kept the pilot sites first.",
                    assessment="The two sequences cannot both hold.",
                    evidence_ids=[PILOT_SEQUENCING_SLACK, PILOT_SEQUENCING_CALL],
                    confidence=Confidence.MEDIUM,
                )
            ]
        }
    )
    strategy = agents.outputs[NS]
    [first, second] = strategy.next_actions
    sequenced = first.model_copy(
        update={"evidence_ids": [PILOT_SEQUENCING_SLACK, PILOT_SEQUENCING_CALL]}
    )
    agents.outputs[NS] = strategy.model_copy(update={"next_actions": [sequenced, second]})

    record = run_bench.run(agents, "USR-5001", "OPP-1001")

    assert record.state is RunState.AWAITING_APPROVAL
    assert approvals_by_subject(run_bench, record.run_id) == [
        (
            "action:A1",
            ApproverRole.HUMAN_REVIEWER.value,
            [RuleId.R7.value],
            ApprovalStatus.PENDING.value,
        )
    ]


def test_low_confidence_action_routes_to_a_human_reviewer(run_bench) -> None:
    agents = run_bench.agents("OPP-1001")
    strategy = agents.outputs[NS]
    [first, second] = strategy.next_actions
    unsure = first.model_copy(update={"confidence": Confidence.LOW})
    agents.outputs[NS] = strategy.model_copy(update={"next_actions": [unsure, second]})

    record = run_bench.run(agents, "USR-5001", "OPP-1001")

    assert approvals_by_subject(run_bench, record.run_id) == [
        (
            "action:A1",
            ApproverRole.HUMAN_REVIEWER.value,
            [RuleId.R7.value],
            ApprovalStatus.PENDING.value,
        )
    ]


def test_reader_who_cannot_request_approvals_gets_labels_and_no_rows(run_bench) -> None:
    agents = run_bench.agents("OPP-1001")
    strategy = agents.outputs[NS]
    [first, second] = strategy.next_actions
    unsure = first.model_copy(update={"confidence": Confidence.LOW})
    agents.outputs[NS] = strategy.model_copy(update={"next_actions": [unsure, second]})

    record = run_bench.run(agents, "USR-5007", "OPP-1001")

    assert record.state is RunState.COMPLETED
    assert approvals_by_subject(run_bench, record.run_id) == []
    policy = stored_policy(run_bench, record.run_id)
    assert (policy.requestable, policy.approval_ids) == (False, [])
    brief = run_bench.briefs(record.run_id)[-1]
    assert "A1" in brief.markdown
    assert "[REQUIRES APPROVAL; you are not permitted to request it]" in brief.markdown
