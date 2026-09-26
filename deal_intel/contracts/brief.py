"""The rendered brief: one typed field per section, plus metadata kept out of the Markdown body."""

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import Field, JsonValue

from deal_intel.contracts.access import AccessLevel
from deal_intel.contracts.agents.common import AgentName
from deal_intel.contracts.agents.deal_snapshot import PricingVisibility
from deal_intel.contracts.agents.stakeholder_map import RoleInDeal
from deal_intel.contracts.approvals import ApproverRole, RuleId
from deal_intel.contracts.base import StrictModel
from deal_intel.contracts.guardrails import Confidence

MAX_EXCERPT_CHARS = 200

SECTION_HEADINGS: tuple[str, ...] = (
    "Deal Snapshot",
    "Executive Summary",
    "Buyer Goals and Business Drivers",
    "Stakeholder Map",
    "Negotiation State",
    "Recommended Next Actions",
    "Missing Information",
    "Source Evidence",
    "Confidence and Review Warnings",
)


class BriefSource(StrEnum):
    RUN = "run"
    REPLAY = "replay"
    APPROVAL_UPDATE = "approval_update"


class LabelKind(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    ESCALATED = "escalated"
    EXPIRED = "expired"
    NOT_REQUESTABLE = "not_requestable"


class ApprovalLabel(StrictModel):
    kind: LabelKind
    role: ApproverRole | None = None
    actor_user_id: str | None = None
    decided_on: date | None = None


class ClaimLine(StrictModel):
    text: str
    citations: list[str] = Field(min_length=1)


class StakeholderLine(StrictModel):
    name: str
    title: str
    role_in_deal: RoleInDeal
    influence: str
    sentiment: str
    stance: str
    citations: list[str] = Field(min_length=1)


class DealSnapshotSection(StrictModel):
    lines: list[ClaimLine]
    pricing_notes: list[ClaimLine]
    pricing_visibility: PricingVisibility


class ExecutiveSummarySection(StrictModel):
    sentences: list[ClaimLine]


class BuyerGoalsSection(StrictModel):
    goals: list[ClaimLine]
    drivers: list[ClaimLine]
    objections: list[ClaimLine]
    competitors: list[ClaimLine]


class StakeholderSection(StrictModel):
    stakeholders: list[StakeholderLine]
    off_crm_people: list[ClaimLine]
    unmatched_speakers: list[ClaimLine]
    roles_missing: list[RoleInDeal]


class NegotiationStateSection(StrictModel):
    assessment: ClaimLine | None
    customer_position: ClaimLine | None
    vendor_position: ClaimLine | None
    open_items: list[ClaimLine]
    urgency: ClaimLine | None
    commitments: list[ClaimLine]
    action_items: list[ClaimLine]


class NextActionLine(StrictModel):
    id: str
    action: str
    owner_role: str
    rationale: ClaimLine
    labels: list[ApprovalLabel]
    rule_ids: list[RuleId]
    proposed_values: dict[str, JsonValue]
    customer_facing: bool
    customer_language: str | None
    confidence: Confidence
    citations: list[str] = Field(min_length=1)


class PolicyItemLine(StrictModel):
    """A fired pricing-note rule that no next action carries, so it still reaches the reader."""

    subject_id: str
    summary: str
    labels: list[ApprovalLabel]
    rule_ids: list[RuleId]
    customer_language: str | None
    citations: list[str] = Field(min_length=1)


class NextActionsSection(StrictModel):
    actions: list[NextActionLine]
    policy_items: list[PolicyItemLine]


class MissingInformationSection(StrictModel):
    items: list[str]


class EvidenceEntry(StrictModel):
    chunk_id: str
    citation: str
    excerpt: str = Field(max_length=MAX_EXCERPT_CHARS)


class SourceEvidenceSection(StrictModel):
    entries: list[EvidenceEntry]


class ConfidenceSection(StrictModel):
    agent_confidence: dict[AgentName, Confidence]
    conflicts: list[ClaimLine]
    warnings: list[str]
    degraded_components: list[AgentName]
    escalated: list[str]
    pending: list[str]
    guardrail_counts: dict[str, int]


class BriefMetadata(StrictModel):
    """Everything that may differ between two renders of the same state lives here, never in
    the Markdown body, so replays are byte-identical."""

    run_id: str
    opportunity_id: str
    version: int = Field(ge=1)
    source: BriefSource
    max_access_level: AccessLevel
    degraded: bool
    cost_usd: Decimal
    rendered_at: datetime


class Brief(StrictModel):
    metadata: BriefMetadata
    deal_snapshot: DealSnapshotSection
    executive_summary: ExecutiveSummarySection
    buyer_goals: BuyerGoalsSection
    stakeholder_map: StakeholderSection
    negotiation_state: NegotiationStateSection
    next_actions: NextActionsSection
    missing_information: MissingInformationSection
    source_evidence: SourceEvidenceSection
    confidence: ConfidenceSection


class BriefVersion(StrictModel):
    run_id: str
    version: int
    source: BriefSource
    max_access_level: AccessLevel
    rendered_at: datetime
