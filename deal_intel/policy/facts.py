"""Structured facts the rules read: one per next action and one per permitted pricing note.

Pricing notes are facts in their own right, so a note above a threshold routes an approval even
when no agent mentions it. An action proposing a note's discount while citing that note shares
the note's subject, so approvers decide it once (plan change C12).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from pydantic import JsonValue

from deal_intel.contracts.access import SourceType
from deal_intel.contracts.agents.agent_run import AgentRun
from deal_intel.contracts.agents.conversation_intelligence import ConversationFindings
from deal_intel.contracts.agents.deal_snapshot import CitedRecord
from deal_intel.contracts.agents.negotiation_strategy import (
    NextAction,
    ProposedValues,
    SensitivityTag,
    StrategyOutput,
)
from deal_intel.contracts.approvals import SubjectKind, subject_id
from deal_intel.contracts.evidence import CHUNK_SOURCE_TYPES, chunk_kind_of
from deal_intel.contracts.guardrails import Confidence
from deal_intel.contracts.reference import PricingNote
from deal_intel.contracts.runs import AnalysisOutputs
from deal_intel.retrieval.chunkers.base import percent

# Evidence about the deal itself; policy rule text says what is allowed, not what happened.
PRIMARY_SOURCE_TYPES: frozenset[SourceType] = frozenset(
    {SourceType.SALESFORCE, SourceType.GONG, SourceType.SLACK, SourceType.PRICING}
)
PRICING_NOTE_TAGS: frozenset[SensitivityTag] = frozenset(
    {SensitivityTag.PRICING, SensitivityTag.DISCOUNT}
)
PRICING_NOTE_SUMMARY = (
    "Pricing note {note_id}: requested discount {discount}, renewal uplift {uplift}"
)


@dataclass(frozen=True)
class RecommendationFacts:
    kind: SubjectKind
    recommendation_id: str
    subject_id: str
    summary: str
    discount_pct: Decimal | None
    uplift_pct: Decimal | None
    liability_cap_change: bool
    tags: frozenset[SensitivityTag]
    customer_facing: bool
    low_confidence: bool
    has_conflict: bool
    missing_source_data: bool
    proposed_values: dict[str, JsonValue]
    evidence_ids: tuple[str, ...]


def facts_from_outputs(analysis: AnalysisOutputs) -> list[RecommendationFacts]:
    notes = [pricing_note_facts(cited) for cited in analysis.snapshot.pricing_notes]
    degraded = analysis.findings is None or analysis.stakeholders is None
    return [*notes, *action_facts(analysis.strategy, analysis.findings, notes, degraded)]


def pricing_note_facts(cited: CitedRecord[PricingNote]) -> RecommendationFacts:
    note = cited.record
    values = ProposedValues(discount_pct=note.requested_discount, uplift_pct=note.renewal_uplift)
    note_subject = subject_id(SubjectKind.PRICING, note.pricing_note_id)
    return RecommendationFacts(
        kind=SubjectKind.PRICING,
        recommendation_id=note_subject,
        subject_id=note_subject,
        summary=PRICING_NOTE_SUMMARY.format(
            note_id=note.pricing_note_id,
            discount=percent(note.requested_discount),
            uplift=percent(note.renewal_uplift),
        ),
        discount_pct=note.requested_discount,
        uplift_pct=note.renewal_uplift,
        liability_cap_change=False,
        tags=PRICING_NOTE_TAGS,
        customer_facing=False,
        low_confidence=False,
        has_conflict=False,
        missing_source_data=False,
        proposed_values=proposed_values_json(values),
        evidence_ids=(cited.evidence_id,),
    )


def action_facts(
    strategy: AgentRun[StrategyOutput],
    findings: AgentRun[ConversationFindings] | None,
    notes: Sequence[RecommendationFacts],
    degraded: bool,
) -> list[RecommendationFacts]:
    conflicted = conflicted_ids(findings)
    primary = primary_ids(strategy)
    return [
        facts_for_action(action, notes, conflicted, primary, degraded)
        for action in strategy.output.next_actions
    ]


def facts_for_action(
    action: NextAction,
    notes: Sequence[RecommendationFacts],
    conflicted: frozenset[str],
    primary: frozenset[str],
    degraded: bool,
) -> RecommendationFacts:
    values = action.proposed_values
    cited = frozenset(action.evidence_ids)
    own_subject = action_subject_id(action.id)
    discount = values.discount_pct if values else None
    return RecommendationFacts(
        kind=SubjectKind.ACTION,
        recommendation_id=own_subject,
        subject_id=shared_note_subject(discount, cited, notes) or own_subject,
        summary=action.action,
        discount_pct=discount,
        uplift_pct=values.uplift_pct if values else None,
        liability_cap_change=values is not None and values.liability_cap_change is not None,
        tags=frozenset(action.sensitivity_tags),
        customer_facing=action.customer_facing,
        low_confidence=action.confidence is Confidence.LOW
        or SensitivityTag.LOW_CONFIDENCE in action.sensitivity_tags,
        has_conflict=bool(cited & conflicted),
        # Plan change C13: degraded inputs, or no primary evidence behind the action.
        missing_source_data=degraded or not (cited & primary),
        proposed_values=proposed_values_json(values) if values else {},
        evidence_ids=tuple(action.evidence_ids),
    )


def action_subject_id(action_id: str) -> str:
    return subject_id(SubjectKind.ACTION, action_id)


def shared_note_subject(
    discount: Decimal | None, cited: frozenset[str], notes: Sequence[RecommendationFacts]
) -> str | None:
    if discount is None:
        return None
    return next(
        (
            note.subject_id
            for note in notes
            if note.discount_pct == discount and cited.intersection(note.evidence_ids)
        ),
        None,
    )


def conflicted_ids(findings: AgentRun[ConversationFindings] | None) -> frozenset[str]:
    if findings is None:
        return frozenset()
    return frozenset(
        chunk_id for conflict in findings.output.conflicts for chunk_id in conflict.evidence_ids
    )


def primary_ids(strategy: AgentRun[StrategyOutput]) -> frozenset[str]:
    """Deal evidence the strategy agent itself retrieved, in its pack or through its tools."""
    return frozenset(
        chunk_id
        for chunk_id in strategy.citable_ids()
        if CHUNK_SOURCE_TYPES[chunk_kind_of(chunk_id)] in PRIMARY_SOURCE_TYPES
    )


def proposed_values_json(values: ProposedValues) -> dict[str, JsonValue]:
    return values.model_dump(mode="json", exclude_none=True)
