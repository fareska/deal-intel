"""Brief to Markdown. Reads only the section fields, never the metadata, so two renders of the
same state are byte-identical."""

from collections.abc import Iterable, Sequence

from deal_intel.contracts.brief import (
    SECTION_HEADINGS,
    Brief,
    BuyerGoalsSection,
    ClaimLine,
    ConfidenceSection,
    DealSnapshotSection,
    ExecutiveSummarySection,
    MissingInformationSection,
    NegotiationStateSection,
    NextActionLine,
    NextActionsSection,
    PolicyItemLine,
    SourceEvidenceSection,
    StakeholderSection,
)
from deal_intel.rendering.labels import labels_text

TITLE = "# Negotiation Brief: {opportunity_id}"
HEADING = "## {title}"
GROUP = "**{title}**"
BULLET = "- {text}"
SUB_BULLET = "  - {text}"
NONE_LINE = "- None."
BLANK = ""
NEWLINE = "\n"


def to_markdown(brief: Brief) -> str:
    bodies = (
        snapshot_lines(brief.deal_snapshot),
        summary_lines(brief.executive_summary),
        buyer_goal_lines(brief.buyer_goals),
        stakeholder_lines(brief.stakeholder_map),
        negotiation_lines(brief.negotiation_state),
        next_action_lines(brief.next_actions),
        missing_lines(brief.missing_information),
        evidence_lines(brief.source_evidence),
        confidence_lines(brief.confidence),
    )
    lines = [TITLE.format(opportunity_id=brief.metadata.opportunity_id)]
    for title, body in zip(SECTION_HEADINGS, bodies, strict=True):
        lines += [BLANK, HEADING.format(title=title), BLANK, *(body or [NONE_LINE])]
    return NEWLINE.join(lines) + NEWLINE


def cited(text: str, citations: Sequence[str]) -> str:
    return " ".join([text, *(f"[{citation}]" for citation in citations)])


def claim_bullets(claims: Iterable[ClaimLine | None]) -> list[str]:
    return [BULLET.format(text=cited(claim.text, claim.citations)) for claim in claims if claim]


def group(title: str, lines: Sequence[str]) -> list[str]:
    """A titled group inside a section; empty groups are left out."""
    return [GROUP.format(title=title), *lines] if lines else []


def plain_bullets(items: Iterable[str]) -> list[str]:
    return [BULLET.format(text=item) for item in items]


def snapshot_lines(section: DealSnapshotSection) -> list[str]:
    return [
        *claim_bullets(section.lines),
        *group("Pricing notes", claim_bullets(section.pricing_notes)),
        BULLET.format(text=f"Pricing visibility: {section.pricing_visibility.value}"),
    ]


def summary_lines(section: ExecutiveSummarySection) -> list[str]:
    return claim_bullets(section.sentences)


def buyer_goal_lines(section: BuyerGoalsSection) -> list[str]:
    return [
        *group("Buyer goals", claim_bullets(section.goals)),
        *group("Business drivers", claim_bullets(section.drivers)),
        *group("Objections", claim_bullets(section.objections)),
        *group("Competitors", claim_bullets(section.competitors)),
    ]


def stakeholder_lines(section: StakeholderSection) -> list[str]:
    people = [
        BULLET.format(
            text=cited(
                f"{person.name}, {person.title} ({person.role_in_deal.value}; influence "
                f"{person.influence}; sentiment {person.sentiment}): {person.stance}",
                person.citations,
            )
        )
        for person in section.stakeholders
    ]
    roles = [role.value for role in section.roles_missing]
    return [
        *people,
        *group("People without a CRM record", claim_bullets(section.off_crm_people)),
        *group("Unmatched call speakers", claim_bullets(section.unmatched_speakers)),
        *group("Committee roles not identified", plain_bullets(roles)),
    ]


def negotiation_lines(section: NegotiationStateSection) -> list[str]:
    return [
        *claim_bullets([section.assessment, section.customer_position, section.vendor_position]),
        *group("Open items", claim_bullets(section.open_items)),
        *group("Urgency", claim_bullets([section.urgency])),
        *group("Commitments", claim_bullets(section.commitments)),
        *group("Action items", claim_bullets(section.action_items)),
    ]


def next_action_lines(section: NextActionsSection) -> list[str]:
    actions = [line for action in section.actions for line in action_lines(action)]
    items = [line for item in section.policy_items for line in policy_item_lines(item)]
    return [*actions, *group("Policy items not covered by an action", items)]


def action_lines(action: NextActionLine) -> list[str]:
    heading = " ".join(
        part
        for part in (
            f"{action.id} ({action.owner_role}, confidence {action.confidence.value}):",
            action.action,
            labels_text(action.labels),
        )
        if part
    )
    return [
        BULLET.format(text=cited(heading, action.citations)),
        SUB_BULLET.format(
            text=cited(f"Rationale: {action.rationale.text}", action.rationale.citations)
        ),
        *detail_lines(action.rule_ids, action.proposed_values, action.customer_language),
    ]


def policy_item_lines(item: PolicyItemLine) -> list[str]:
    heading = " ".join(part for part in (item.summary, labels_text(item.labels)) if part)
    return [
        BULLET.format(text=cited(heading, item.citations)),
        *detail_lines(item.rule_ids, {}, item.customer_language),
    ]


def detail_lines(
    rule_ids: Sequence[str], proposed_values: dict[str, object], customer_language: str | None
) -> list[str]:
    lines = []
    if rule_ids:
        lines.append(SUB_BULLET.format(text=f"Policy rules: {', '.join(rule_ids)}"))
    if proposed_values:
        values = ", ".join(f"{name} {value}" for name, value in proposed_values.items())
        lines.append(SUB_BULLET.format(text=f"Proposed values: {values}"))
    if customer_language:
        lines.append(SUB_BULLET.format(text=f"Customer language: {customer_language}"))
    return lines


def missing_lines(section: MissingInformationSection) -> list[str]:
    return plain_bullets(section.items)


def evidence_lines(section: SourceEvidenceSection) -> list[str]:
    return [BULLET.format(text=f"[{entry.citation}] {entry.excerpt}") for entry in section.entries]


def confidence_lines(section: ConfidenceSection) -> list[str]:
    confidence = [
        f"{agent.value}: {level.value}" for agent, level in section.agent_confidence.items()
    ]
    counts = [f"{key}: {count}" for key, count in section.guardrail_counts.items()]
    return [
        *group("Escalated", plain_bullets(section.escalated)),
        *group("Pending approval", plain_bullets(section.pending)),
        *group(
            "Degraded components", plain_bullets(name.value for name in section.degraded_components)
        ),
        *group("Warnings", plain_bullets(section.warnings)),
        *group("Conflicting evidence", claim_bullets(section.conflicts)),
        *group("Agent confidence", plain_bullets(confidence)),
        *group("Guardrail results", plain_bullets(counts)),
    ]
