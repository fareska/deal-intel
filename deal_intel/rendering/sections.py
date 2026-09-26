"""Builds each brief section from stored outputs. Pure: no database and no clock, so the same
inputs always give the same sections."""

from collections import Counter
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass

from pydantic import BaseModel

from deal_intel.contracts.access import AccessScope
from deal_intel.contracts.agents.common import AgentName
from deal_intel.contracts.agents.conversation_intelligence import (
    ActionItem,
    Conflict,
    ConversationFindings,
)
from deal_intel.contracts.agents.deal_snapshot import CitedRecord, DealSnapshot, PricingVisibility
from deal_intel.contracts.agents.negotiation_strategy import (
    NextAction,
    StrategyOutput,
    missing_subagents,
)
from deal_intel.contracts.agents.stakeholder_map import OffCrmPerson, RoleInDeal, StakeholderMap
from deal_intel.contracts.approvals import (
    ApprovalStatus,
    ApprovalView,
    PolicyOutput,
    RuleId,
    SubjectKind,
)
from deal_intel.contracts.brief import (
    MAX_EXCERPT_CHARS,
    ApprovalLabel,
    BuyerGoalsSection,
    ClaimLine,
    ConfidenceSection,
    DealSnapshotSection,
    EvidenceEntry,
    ExecutiveSummarySection,
    MissingInformationSection,
    NegotiationStateSection,
    NextActionLine,
    NextActionsSection,
    PolicyItemLine,
    SourceEvidenceSection,
    StakeholderLine,
    StakeholderSection,
)
from deal_intel.contracts.evidence import ChunkKind, PackChunk
from deal_intel.contracts.guardrails import (
    Confidence,
    EvidenceBacked,
    GuardrailOutcome,
    GuardrailResult,
)
from deal_intel.contracts.runs import AnalysisOutputs, GuardrailsOutput, RetrieveOutput
from deal_intel.guardrails.render_checks import indexed_ref, item_ref, lint_customer_facing
from deal_intel.guardrails.text import normalise_whitespace
from deal_intel.guardrails.validators import cited_fields
from deal_intel.policy.facts import RecommendationFacts, action_subject_id, facts_from_outputs
from deal_intel.policy.rules import routes_to_approver
from deal_intel.rendering.evidence import chunks_by_citation
from deal_intel.rendering.labels import NOT_REQUESTABLE, all_approved, label_for, label_text
from deal_intel.rendering.language import approved_customer_language

CI = AgentName.CONVERSATION_INTELLIGENCE
SM = AgentName.STAKEHOLDER_MAP
NS = AgentName.NEGOTIATION_STRATEGY
# Ties resolve to the lower confidence, the safer reading for a reviewer.
CONFIDENCE_ORDER: tuple[Confidence, ...] = (Confidence.LOW, Confidence.MEDIUM, Confidence.HIGH)
AGENT_TITLES: dict[AgentName, str] = {
    AgentName.DEAL_SNAPSHOT: "Deal snapshot",
    CI: "Conversation intelligence",
    SM: "Stakeholder mapping",
    NS: "Negotiation strategy",
}
EXCERPT_ELLIPSIS = "..."
DEGRADED_WARNING = "{agent} did not complete; this brief was produced without it."
DEGRADED_MISSING = "{agent} output is unavailable for this run."
WITHHELD_WARNING = (
    "{count} item(s) were left out because they state an approval the approval records do not show."
)
CUSTOMER_TEXT_WARNING = "Customer-facing language for {ref} was withheld."
NO_SLACK = "No Slack account-team updates were available."
NO_PRICING = "No pricing notes are available in your view."
NO_LEGAL_STAKEHOLDER = "No legal stakeholder has been identified."
NO_ACTION_ITEMS = "No dated action items were found in calls or Slack updates."
CONFLICT_TEXT = "{topic}: {claim_a} / {claim_b} ({assessment})"
OFF_CRM_TEXT = "{who} ({role}): {stance}"
URGENCY_TEXT = "{level}: {rationale}"
ACTION_ITEM_TEXT = "{description} ({details})"
DETAIL_SEPARATOR = ", "
DUE_PREFIX = "due "
NEXT_ACTIONS_FIELD = "next_actions"
CITATIONS_FIELD = "citations"
COUNT_KEY_SEPARATOR = "/"


@dataclass(frozen=True)
class BriefInputs:
    scope: AccessScope
    retrieval: RetrieveOutput
    analysis: AnalysisOutputs
    policy: PolicyOutput
    guardrails: GuardrailsOutput
    approvals: Sequence[ApprovalView]
    chunks: Mapping[str, PackChunk]


@dataclass(frozen=True)
class Citer:
    """Turns cited ids into citation strings and skips items the guardrails withheld."""

    chunks: Mapping[str, PackChunk]
    withheld: frozenset[str]

    def citations(self, evidence_ids: Iterable[str]) -> list[str]:
        return [
            self.chunks[chunk_id].citation for chunk_id in evidence_ids if chunk_id in self.chunks
        ]

    def claim(self, text: str, evidence_ids: Iterable[str]) -> ClaimLine | None:
        citations = self.citations(evidence_ids)
        return ClaimLine(text=text, citations=citations) if citations else None

    def kept[ItemT: EvidenceBacked](
        self, agent: AgentName, field: str, items: Sequence[ItemT]
    ) -> list[ItemT]:
        return [
            item
            for index, item in enumerate(items)
            if indexed_ref(agent, field, index) not in self.withheld
        ]

    def kept_one[ItemT: EvidenceBacked](
        self, agent: AgentName, field: str, item: ItemT | None
    ) -> ItemT | None:
        return None if item is None or item_ref(agent, field) in self.withheld else item


def present[LineT](lines: Iterable[LineT | None]) -> list[LineT]:
    return [line for line in lines if line is not None]


def citer_for(inputs: BriefInputs) -> Citer:
    return Citer(chunks=inputs.chunks, withheld=frozenset(inputs.guardrails.withheld_item_refs))


def chunk_fields(chunk: PackChunk) -> list[str]:
    """A reference chunk is a heading line then one `Label: value` line per field."""
    return chunk.text.splitlines()[1:]


def snapshot_section(snapshot: DealSnapshot, citer: Citer) -> DealSnapshotSection:
    """Facts are the evidence lines themselves, so the brief states exactly what the cited
    chunk says."""
    lines = [
        citer.claim(field, [block.evidence_id])
        for block in (snapshot.opportunity, snapshot.account)
        if block.evidence_id in citer.chunks
        for field in chunk_fields(citer.chunks[block.evidence_id])
    ]
    return DealSnapshotSection(
        lines=present(lines),
        pricing_notes=present(pricing_line(note, citer) for note in snapshot.pricing_notes),
        pricing_visibility=snapshot.pricing_visibility,
    )


def pricing_line(note: CitedRecord, citer: Citer) -> ClaimLine | None:
    chunk = citer.chunks.get(note.evidence_id)
    if chunk is None:
        return None
    heading, *fields = chunk.text.splitlines()
    return citer.claim(f"{heading}: {'; '.join(fields)}", [note.evidence_id])


def executive_summary_section(strategy: StrategyOutput, citer: Citer) -> ExecutiveSummarySection:
    sentences = citer.kept(NS, "executive_summary", strategy.executive_summary)
    return ExecutiveSummarySection(
        sentences=present(citer.claim(item.text, item.evidence_ids) for item in sentences)
    )


def buyer_goals_section(findings: ConversationFindings | None, citer: Citer) -> BuyerGoalsSection:
    if findings is None:
        return BuyerGoalsSection(goals=[], drivers=[], objections=[], competitors=[])

    def statements(field: str) -> list[ClaimLine]:
        items = citer.kept(CI, field, getattr(findings, field))
        return present(citer.claim(item.statement, item.evidence_ids) for item in items)

    return BuyerGoalsSection(
        goals=statements("buyer_goals"),
        drivers=statements("business_drivers"),
        objections=statements("objections"),
        competitors=statements("competitor_mentions"),
    )


def stakeholder_section(stakeholders: StakeholderMap | None, citer: Citer) -> StakeholderSection:
    if stakeholders is None:
        return StakeholderSection(
            stakeholders=[], off_crm_people=[], unmatched_speakers=[], roles_missing=[]
        )
    people = [
        StakeholderLine(
            name=person.name,
            title=person.title,
            role_in_deal=person.role_in_deal,
            influence=person.influence.value,
            sentiment=person.sentiment,
            stance=person.stance_summary,
            citations=citer.citations(person.evidence_ids),
        )
        for person in citer.kept(SM, "stakeholders", stakeholders.stakeholders)
        if citer.citations(person.evidence_ids)
    ]
    off_crm = citer.kept(SM, "off_crm_people", stakeholders.off_crm_people)
    unmatched = citer.kept(SM, "unmatched_speakers", stakeholders.unmatched_speakers)
    return StakeholderSection(
        stakeholders=people,
        off_crm_people=present(off_crm_line(person, citer) for person in off_crm),
        unmatched_speakers=present(
            citer.claim(speaker.name, speaker.evidence_ids) for speaker in unmatched
        ),
        roles_missing=stakeholders.roles_missing(),
    )


def off_crm_line(person: OffCrmPerson, citer: Citer) -> ClaimLine | None:
    text = OFF_CRM_TEXT.format(
        who=person.name or person.description,
        role=person.role_in_deal.value,
        stance=person.stance_summary,
    )
    return citer.claim(text, person.evidence_ids)


def negotiation_state_section(
    strategy: StrategyOutput, findings: ConversationFindings | None, citer: Citer
) -> NegotiationStateSection:
    state = citer.kept_one(NS, "negotiation_state", strategy.negotiation_state)
    ids = state.evidence_ids if state else []
    urgency = citer.kept_one(CI, "urgency", findings.urgency) if findings else None
    commitments = citer.kept(CI, "commitments", findings.commitments) if findings else []
    action_items = citer.kept(CI, "action_items", findings.action_items) if findings else []
    return NegotiationStateSection(
        assessment=citer.claim(state.stage_assessment, ids) if state else None,
        customer_position=citer.claim(state.customer_position, ids) if state else None,
        vendor_position=citer.claim(state.vendor_position, ids) if state else None,
        open_items=present(citer.claim(item, ids) for item in state.open_items) if state else [],
        urgency=citer.claim(
            URGENCY_TEXT.format(level=urgency.level.value, rationale=urgency.rationale),
            urgency.evidence_ids,
        )
        if urgency
        else None,
        commitments=present(citer.claim(item.statement, item.evidence_ids) for item in commitments),
        action_items=present(action_item_line(item, citer) for item in action_items),
    )


def action_item_line(item: ActionItem, citer: Citer) -> ClaimLine | None:
    details = [
        item.side.value,
        item.owner,
        f"{DUE_PREFIX}{item.due_date}" if item.due_date else None,
    ]
    text = ACTION_ITEM_TEXT.format(
        description=item.description, details=DETAIL_SEPARATOR.join(present(details))
    )
    return citer.claim(text, item.evidence_ids)


@dataclass(frozen=True)
class ActionsBuild:
    section: NextActionsSection
    results: list[GuardrailResult]


def next_actions_section(inputs: BriefInputs, citer: Citer) -> ActionsBuild:
    facts = {item.recommendation_id: item for item in facts_from_outputs(inputs.analysis)}
    actions = inputs.analysis.strategy.output.next_actions
    kept = [
        (index, action)
        for index, action in enumerate(actions)
        if indexed_ref(NS, NEXT_ACTIONS_FIELD, index) not in citer.withheld
    ]
    built = [action_line(action, index, facts, inputs, citer) for index, action in kept]
    carried = {facts[action_subject_id(action.id)].subject_id for _, action in kept}
    items = [
        policy_item_line(item, inputs, citer)
        for item in facts.values()
        if item.kind is SubjectKind.PRICING
        and item.subject_id not in carried
        and item.recommendation_id in inputs.policy.fired_rules
    ]
    return ActionsBuild(
        section=NextActionsSection(
            actions=present(line for line, _ in built),
            policy_items=present(line for line, _ in items),
        ),
        results=[result for _, results in [*built, *items] for result in results],
    )


def action_line(
    action: NextAction,
    index: int,
    facts: Mapping[str, RecommendationFacts],
    inputs: BriefInputs,
    citer: Citer,
) -> tuple[NextActionLine | None, list[GuardrailResult]]:
    recommendation_id = action_subject_id(action.id)
    rule_ids = inputs.policy.fired_rules.get(recommendation_id, [])
    linked = linked_approvals(inputs.approvals, recommendation_id)
    rationale = citer.claim(action.rationale, action.evidence_ids)
    if rationale is None:
        return None, []
    ref = indexed_ref(NS, NEXT_ACTIONS_FIELD, index)
    text, results = action.action, []
    if action.customer_facing:
        lint = lint_customer_facing(action.action, all_approved(linked), ref)
        text, results = lint.text, list(lint.results)
    language, language_results = approved_language(linked, ref)
    line = NextActionLine(
        id=action.id,
        action=text,
        owner_role=action.owner_role,
        rationale=rationale,
        labels=labels_for(linked, rule_ids, inputs.policy.requestable),
        rule_ids=rule_ids,
        proposed_values=facts[recommendation_id].proposed_values,
        customer_facing=action.customer_facing,
        customer_language=language,
        confidence=action.confidence,
        citations=rationale.citations,
    )
    return line, [*results, *language_results]


def policy_item_line(
    item: RecommendationFacts, inputs: BriefInputs, citer: Citer
) -> tuple[PolicyItemLine | None, list[GuardrailResult]]:
    citations = citer.citations(item.evidence_ids)
    if not citations:
        return None, []
    linked = linked_approvals(inputs.approvals, item.recommendation_id)
    rule_ids = inputs.policy.fired_rules[item.recommendation_id]
    language, results = approved_language(linked, item.subject_id)
    line = PolicyItemLine(
        subject_id=item.subject_id,
        summary=item.summary,
        labels=labels_for(linked, rule_ids, inputs.policy.requestable),
        rule_ids=rule_ids,
        customer_language=language,
        citations=citations,
    )
    return line, results


def linked_approvals(
    approvals: Sequence[ApprovalView], recommendation_id: str
) -> list[ApprovalView]:
    return [view for view in approvals if recommendation_id in view.approval.recommendation_ids]


def labels_for(
    linked: Sequence[ApprovalView], rule_ids: Sequence[RuleId], requestable: bool
) -> list[ApprovalLabel]:
    if not requestable and routes_to_approver(rule_ids):
        return [NOT_REQUESTABLE]
    return [label_for(view) for view in linked]


def approved_language(
    linked: Sequence[ApprovalView], ref: str
) -> tuple[str | None, list[GuardrailResult]]:
    if not all_approved(linked):
        return None, []
    rule_ids = sorted({rule for view in linked for rule in view.approval.rule_ids})
    language = approved_customer_language(rule_ids, linked[0].approval.proposed_values)
    if language is None:
        return None, []
    lint = lint_customer_facing(language, approved=True, ref=ref)
    return lint.text, list(lint.results)


def degraded_components(analysis: AnalysisOutputs) -> list[AgentName]:
    return missing_subagents(
        analysis.findings.output if analysis.findings else None,
        analysis.stakeholders.output if analysis.stakeholders else None,
    )


def missing_information_section(inputs: BriefInputs) -> MissingInformationSection:
    analysis = inputs.analysis
    agent_notes = [
        *analysis.strategy.output.missing_information,
        *(analysis.findings.output.missing if analysis.findings else []),
        *(analysis.stakeholders.output.missing if analysis.stakeholders else []),
    ]
    return MissingInformationSection(items=unique_folded([*agent_notes, *coverage_gaps(inputs)]))


def coverage_gaps(inputs: BriefInputs) -> list[str]:
    """Gaps code can see for itself, so the section never depends on a model noticing them."""
    analysis = inputs.analysis
    gaps = [
        DEGRADED_MISSING.format(agent=AGENT_TITLES[name]) for name in degraded_components(analysis)
    ]
    if not any(chunk.kind is ChunkKind.SLACK for chunk in evidence_seen(inputs)):
        gaps.append(NO_SLACK)
    if analysis.snapshot.pricing_visibility is PricingVisibility.NONE:
        gaps.append(NO_PRICING)
    stakeholders = analysis.stakeholders
    if stakeholders and RoleInDeal.LEGAL in stakeholders.output.roles_missing():
        gaps.append(NO_LEGAL_STAKEHOLDER)
    if analysis.findings and not analysis.findings.output.action_items:
        gaps.append(NO_ACTION_ITEMS)
    return gaps


def evidence_seen(inputs: BriefInputs) -> list[PackChunk]:
    packed = [chunk for build in inputs.retrieval.packs.values() for chunk in build.pack.chunks]
    return [*packed, *inputs.chunks.values()]


def unique_folded(items: Iterable[str]) -> list[str]:
    seen: dict[str, str] = {}
    for item in items:
        seen.setdefault(normalise_whitespace(item).casefold(), item)
    return list(seen.values())


def source_evidence_section(
    sections: Sequence[BaseModel], chunks: Mapping[str, PackChunk]
) -> SourceEvidenceSection:
    by_citation = chunks_by_citation(chunks)
    cited = dict.fromkeys(
        citation for section in sections for citation in collect_citations(section)
    )
    return SourceEvidenceSection(
        entries=[
            evidence_entry(by_citation[citation]) for citation in cited if citation in by_citation
        ]
    )


def collect_citations(value: object) -> Iterator[str]:
    """In section order, so Source Evidence lists chunks in the order the brief first cites them."""
    if isinstance(value, BaseModel):
        for name, field in value:
            if name == CITATIONS_FIELD:
                yield from field
            else:
                yield from collect_citations(field)
    elif isinstance(value, list):
        for item in value:
            yield from collect_citations(item)


def evidence_entry(chunk: PackChunk) -> EvidenceEntry:
    return EvidenceEntry(
        chunk_id=chunk.chunk_id, citation=chunk.citation, excerpt=excerpt(chunk.text)
    )


def excerpt(text: str) -> str:
    flat = normalise_whitespace(text)
    if len(flat) <= MAX_EXCERPT_CHARS:
        return flat
    return flat[: MAX_EXCERPT_CHARS - len(EXCERPT_ELLIPSIS)].rstrip() + EXCERPT_ELLIPSIS


def confidence_section(
    inputs: BriefInputs, citer: Citer, render_results: Sequence[GuardrailResult]
) -> ConfidenceSection:
    analysis = inputs.analysis
    conflicts = analysis.findings.output.conflicts if analysis.findings else []
    return ConfidenceSection(
        agent_confidence=agent_confidence(analysis),
        conflicts=present(conflict_line(conflict, citer) for conflict in conflicts),
        warnings=review_warnings(inputs, render_results),
        degraded_components=degraded_components(analysis),
        escalated=approval_notices(inputs.approvals, ApprovalStatus.ESCALATED),
        pending=approval_notices(inputs.approvals, ApprovalStatus.PENDING),
        guardrail_counts=guardrail_counts(all_results(inputs, render_results)),
    )


def agent_confidence(analysis: AnalysisOutputs) -> dict[AgentName, Confidence]:
    levels = {
        run.agent_name: [
            item.confidence for cited in cited_fields(run.output) for item in cited.items
        ]
        for run in analysis.agent_runs()
    }
    return {agent: majority(values) for agent, values in levels.items() if values}


def majority(levels: Sequence[Confidence]) -> Confidence:
    counts = Counter(levels)
    return max(CONFIDENCE_ORDER, key=lambda level: (counts[level], -CONFIDENCE_ORDER.index(level)))


def conflict_line(conflict: Conflict, citer: Citer) -> ClaimLine | None:
    text = CONFLICT_TEXT.format(
        topic=conflict.topic,
        claim_a=conflict.claim_a,
        claim_b=conflict.claim_b,
        assessment=conflict.assessment,
    )
    return citer.claim(text, conflict.evidence_ids)


def review_warnings(inputs: BriefInputs, render_results: Sequence[GuardrailResult]) -> list[str]:
    analysis = inputs.analysis
    warnings = [
        DEGRADED_WARNING.format(agent=AGENT_TITLES[name]) for name in degraded_components(analysis)
    ]
    withheld = inputs.guardrails.withheld_item_refs
    if withheld:
        warnings.append(WITHHELD_WARNING.format(count=len(withheld)))
    warnings += [
        CUSTOMER_TEXT_WARNING.format(ref=result.item_ref)
        for result in render_results
        if result.outcome is GuardrailOutcome.WARNING
    ]
    warnings += [
        *analysis.strategy.output.review_warnings,
        *(analysis.findings.output.review_notes if analysis.findings else []),
        *(analysis.stakeholders.output.review_notes if analysis.stakeholders else []),
    ]
    return unique_folded(warnings)


def approval_notices(approvals: Sequence[ApprovalView], status: ApprovalStatus) -> list[str]:
    return [
        f"{label_text(label_for(view))} {view.approval.summary}"
        for view in approvals
        if view.approval.status is status
    ]


def all_results(
    inputs: BriefInputs, render_results: Sequence[GuardrailResult]
) -> list[GuardrailResult]:
    agent_results = [
        result for run in inputs.analysis.agent_runs() for result in run.guardrail_results
    ]
    return [*agent_results, *inputs.guardrails.results, *render_results]


def guardrail_counts(results: Iterable[GuardrailResult]) -> dict[str, int]:
    counts = Counter(
        f"{result.check.value}{COUNT_KEY_SEPARATOR}{result.outcome.value}" for result in results
    )
    return dict(sorted(counts.items()))
