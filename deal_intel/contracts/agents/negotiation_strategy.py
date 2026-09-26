from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Self

from pydantic import AfterValidator, Field, model_validator

from deal_intel.contracts.access import AccessLevel, SourceType
from deal_intel.contracts.agents.common import (
    SUBAGENTS,
    AgentName,
    HarnessFlag,
    Label,
    Note,
    Notes,
    Statement,
)
from deal_intel.contracts.agents.conversation_intelligence import ConversationFindings
from deal_intel.contracts.agents.deal_snapshot import DealSnapshot
from deal_intel.contracts.agents.stakeholder_map import StakeholderMap
from deal_intel.contracts.base import StrictModel
from deal_intel.contracts.guardrails import EvidenceBacked, require_unique

ACTION_ID_PATTERN = r"^A\d{1,2}$"
MIN_SUMMARY_SENTENCES = 3
MAX_SUMMARY_SENTENCES = 6
MAX_SUMMARY_SENTENCE_CHARS = 300
MAX_NEXT_ACTIONS = 10
MAX_PERCENT = Decimal(100)
NO_STRATEGY_EVIDENCE = "no evidence in scope; no strategy was generated"


class SensitivityTag(StrEnum):
    PRICING = "pricing"
    DISCOUNT = "discount"
    LEGAL_TERMS = "legal_terms"
    CUSTOMER_FACING_LANGUAGE = "customer_facing_language"
    DATA_RETENTION = "data_retention"
    RESTRICTED_DATA = "restricted_data"
    LOW_CONFIDENCE = "low_confidence"


PRICING_SENSITIVITY_TAGS: frozenset[SensitivityTag] = frozenset(
    {SensitivityTag.PRICING, SensitivityTag.DISCOUNT}
)
LEGAL_REVIEW_TAGS: frozenset[SensitivityTag] = frozenset(
    {SensitivityTag.LEGAL_TERMS, SensitivityTag.DATA_RETENTION, SensitivityTag.RESTRICTED_DATA}
)


class ProposedValues(StrictModel):
    discount_pct: Decimal | None = Field(default=None, ge=0, le=MAX_PERCENT)
    uplift_pct: Decimal | None = Field(default=None, ge=-MAX_PERCENT, le=MAX_PERCENT)
    term_months: int | None = Field(default=None, gt=0)
    liability_cap_change: Note | None = None

    @model_validator(mode="after")
    def require_a_value(self) -> Self:
        if all(value is None for value in self.model_dump().values()):
            raise ValueError("proposed_values needs at least one value; use null instead")
        return self

    def touches_price(self) -> bool:
        return self.discount_pct is not None or self.uplift_pct is not None


class NextAction(EvidenceBacked):
    id: str = Field(pattern=ACTION_ID_PATTERN)
    action: Note
    owner_role: Label
    rationale: Statement
    sensitivity_tags: Annotated[
        list[SensitivityTag],
        Field(max_length=len(SensitivityTag)),
        AfterValidator(require_unique),
    ]
    customer_facing: bool
    proposed_values: ProposedValues | None = None

    @model_validator(mode="after")
    def keep_pricing_internal(self) -> Self:
        """Approvals are created only after this output exists, so no pricing action can be
        approved yet, and policy rule 6 forbids customer-facing concessions without one."""
        if self.customer_facing and self.is_pricing_sensitive():
            raise ValueError("a pricing or discount action must have customer_facing = false")
        return self

    def is_pricing_sensitive(self) -> bool:
        tagged = bool(PRICING_SENSITIVITY_TAGS.intersection(self.sensitivity_tags))
        priced = self.proposed_values is not None and self.proposed_values.touches_price()
        return tagged or priced


class NegotiationState(EvidenceBacked):
    stage_assessment: Statement
    customer_position: Statement
    vendor_position: Statement
    open_items: Notes = []


class SummarySentence(EvidenceBacked):
    text: str = Field(min_length=1, max_length=MAX_SUMMARY_SENTENCE_CHARS)


class StrategyOutput(StrictModel):
    """`executive_summary` and `negotiation_state` are empty only in the no-evidence case, which
    the harness produces itself; a model output always has 3 to 6 sentences and a state."""

    executive_summary: Annotated[list[SummarySentence], Field(max_length=MAX_SUMMARY_SENTENCES)]
    negotiation_state: NegotiationState | None
    next_actions: Annotated[list[NextAction], Field(max_length=MAX_NEXT_ACTIONS)] = []
    missing_information: Notes = []
    review_warnings: Notes = []
    no_evidence: HarnessFlag = False

    @model_validator(mode="after")
    def require_summary_and_state(self) -> Self:
        if self.no_evidence:
            if self.executive_summary or self.negotiation_state or self.next_actions:
                raise ValueError("a no-evidence output carries no summary, state, or actions")
            return self
        if len(self.executive_summary) < MIN_SUMMARY_SENTENCES:
            raise ValueError(f"executive_summary needs at least {MIN_SUMMARY_SENTENCES} sentences")
        if self.negotiation_state is None:
            raise ValueError("negotiation_state is required")
        return self

    @model_validator(mode="after")
    def require_unique_action_ids(self) -> Self:
        ids = [action.id for action in self.next_actions]
        if len(set(ids)) != len(ids):
            raise ValueError("next action ids must be unique")
        return self

    @classmethod
    def empty(cls) -> Self:
        return cls(
            executive_summary=[],
            negotiation_state=None,
            missing_information=[NO_STRATEGY_EVIDENCE],
            no_evidence=True,
        )


class PolicyThresholds(StrictModel):
    """Field names equal the `Settings` names, which the prompt and the policy engine share."""

    policy_discount_deal_desk_threshold_pct: Decimal
    policy_discount_sales_leader_threshold_pct: Decimal
    policy_renewal_uplift_floor_pct: Decimal


class PolicySummary(PolicyThresholds):
    """Thresholds only; the rule text stays in the evidence pack for users who may read it."""

    legal_review_tags: list[SensitivityTag]


class ScopeFlags(StrictModel):
    source_types: list[SourceType]
    max_access_level: AccessLevel
    policies_allowed: bool
    can_request_approval: bool


def missing_subagents(
    findings: ConversationFindings | None, stakeholders: StakeholderMap | None
) -> list[AgentName]:
    """A subagent that failed has no output; one that found nothing has an empty output."""
    outputs = {
        AgentName.CONVERSATION_INTELLIGENCE: findings,
        AgentName.STAKEHOLDER_MAP: stakeholders,
    }
    return [name for name in SUBAGENTS if outputs[name] is None]


class StrategyContext(StrictModel):
    """Assembled by code, never by a model; serialised as the strategy agent's task context."""

    snapshot: DealSnapshot
    findings: ConversationFindings | None
    stakeholders: StakeholderMap | None
    policy: PolicySummary | None
    scope: ScopeFlags
    degraded_inputs: list[AgentName]

    @model_validator(mode="after")
    def withhold_policy_outside_scope(self) -> Self:
        if self.policy is not None and not self.scope.policies_allowed:
            raise ValueError("policy summary requires policies access")
        return self

    @model_validator(mode="after")
    def require_degraded_inputs_to_match(self) -> Self:
        if self.degraded_inputs != missing_subagents(self.findings, self.stakeholders):
            raise ValueError("degraded_inputs must list exactly the missing subagent outputs")
        return self
