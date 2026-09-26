from datetime import date
from enum import StrEnum
from typing import Annotated, Self

from pydantic import AfterValidator, Field

from deal_intel.contracts.agents.common import (
    MAX_LIST_ITEMS,
    HarnessFlag,
    Label,
    Notes,
    Statement,
)
from deal_intel.contracts.base import StrictModel
from deal_intel.contracts.guardrails import (
    MAX_EVIDENCE_IDS,
    ChunkId,
    EvidenceBacked,
    require_unique,
)
from deal_intel.contracts.reference import LowMediumHigh

# A conflict sets two sources against each other, so one citation cannot show both sides.
MIN_CONFLICT_EVIDENCE_IDS = 2
NO_CONVERSATION_EVIDENCE = "no call or Slack evidence in scope"


class ActionSide(StrEnum):
    CUSTOMER = "customer"
    VENDOR = "vendor"
    JOINT = "joint"


class Finding(EvidenceBacked):
    statement: Statement


class Urgency(EvidenceBacked):
    level: LowMediumHigh
    rationale: Statement


class ActionItem(EvidenceBacked):
    description: Statement
    side: ActionSide
    owner: Label | None = None
    due_date: date | None = None


class Conflict(EvidenceBacked):
    """Both claims are reported as found; `assessment` says why they cannot both hold, never
    which one is right."""

    evidence_ids: Annotated[
        list[ChunkId],
        Field(min_length=MIN_CONFLICT_EVIDENCE_IDS, max_length=MAX_EVIDENCE_IDS),
        AfterValidator(require_unique),
    ]
    topic: Label
    claim_a: Statement
    claim_b: Statement
    assessment: Statement


Findings = Annotated[list[Finding], Field(max_length=MAX_LIST_ITEMS)]


class ConversationFindings(StrictModel):
    buyer_goals: Findings = []
    business_drivers: Findings = []
    objections: Findings = []
    competitor_mentions: Findings = []
    commitments: Findings = []
    # Optional because "no evidence about urgency" belongs in `missing`, not in an uncited item.
    urgency: Urgency | None = None
    action_items: Annotated[list[ActionItem], Field(max_length=MAX_LIST_ITEMS)] = []
    conflicts: Annotated[list[Conflict], Field(max_length=MAX_LIST_ITEMS)] = []
    missing: Notes = []
    review_notes: Notes = []
    no_evidence: HarnessFlag = False

    @classmethod
    def empty(cls) -> Self:
        return cls(missing=[NO_CONVERSATION_EVIDENCE], no_evidence=True)
