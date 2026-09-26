from enum import StrEnum
from typing import Annotated, Self

from pydantic import AfterValidator, Field, model_validator

from deal_intel.contracts.agents.common import MAX_LIST_ITEMS, HarnessFlag, Label, Note, Notes
from deal_intel.contracts.base import StrictModel
from deal_intel.contracts.guardrails import EvidenceBacked
from deal_intel.contracts.reference import CONTACT_ID_PATTERN

MAX_STAKEHOLDERS = 20
VENDOR_SPEAKER_PREFIX = "vendor"
NO_STAKEHOLDER_EVIDENCE = "no contact, call, or Slack evidence in scope"


class RoleInDeal(StrEnum):
    """The brief's vocabulary, not the CRM's; the model maps CRM roles onto it."""

    ECONOMIC_BUYER = "economic_buyer"
    CHAMPION = "champion"
    TECHNICAL_DECISION_MAKER = "technical_decision_maker"
    COMMERCIAL_APPROVER = "commercial_approver"
    LEGAL = "legal"
    BLOCKER = "blocker"
    INFLUENCER = "influencer"
    UNKNOWN = "unknown"


# Blockers, influencers, and unknowns are not seats a buying committee must fill.
EXPECTED_COMMITTEE_ROLES: tuple[RoleInDeal, ...] = (
    RoleInDeal.ECONOMIC_BUYER,
    RoleInDeal.CHAMPION,
    RoleInDeal.TECHNICAL_DECISION_MAKER,
    RoleInDeal.COMMERCIAL_APPROVER,
    RoleInDeal.LEGAL,
)


class Influence(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    UNKNOWN = "unknown"


def reject_vendor_side(value: str) -> str:
    """Transcripts label the seller's people as "Vendor AE", "Vendor Customer Success", ..."""
    words = value.split()
    if words and words[0].casefold() == VENDOR_SPEAKER_PREFIX:
        raise ValueError("vendor-side people are not stakeholders")
    return value


CustomerSideLabel = Annotated[Label, AfterValidator(reject_vendor_side)]


class Stakeholder(EvidenceBacked):
    name: CustomerSideLabel
    title: CustomerSideLabel
    role_in_deal: RoleInDeal
    influence: Influence
    sentiment: Label
    stance_summary: Note
    contact_id: str | None = Field(default=None, pattern=CONTACT_ID_PATTERN)


class UnmatchedSpeaker(EvidenceBacked):
    """A customer-side call speaker or participant with no contact record; `confidence` is how
    sure the model is that the speaker matches no contact."""

    name: CustomerSideLabel


class OffCrmPerson(EvidenceBacked):
    """Someone the evidence describes who has no contact record, possibly without a name."""

    description: CustomerSideLabel
    role_in_deal: RoleInDeal
    stance_summary: Note
    name: CustomerSideLabel | None = None


class StakeholderMap(StrictModel):
    stakeholders: Annotated[list[Stakeholder], Field(max_length=MAX_STAKEHOLDERS)] = []
    unmatched_speakers: Annotated[list[UnmatchedSpeaker], Field(max_length=MAX_LIST_ITEMS)] = []
    off_crm_people: Annotated[list[OffCrmPerson], Field(max_length=MAX_LIST_ITEMS)] = []
    missing: Notes = []
    review_notes: Notes = []
    no_evidence: HarnessFlag = False

    @model_validator(mode="after")
    def require_one_entry_per_contact(self) -> Self:
        contact_ids = [person.contact_id for person in self.stakeholders if person.contact_id]
        if len(set(contact_ids)) != len(contact_ids):
            raise ValueError("each contact_id may appear on one stakeholder only")
        return self

    @classmethod
    def empty(cls) -> Self:
        return cls(missing=[NO_STAKEHOLDER_EVIDENCE], no_evidence=True)

    def roles_missing(self) -> list[RoleInDeal]:
        """Derived rather than asked of the model, so it can never contradict the entries."""
        held = {person.role_in_deal for person in [*self.stakeholders, *self.off_crm_people]}
        return [role for role in EXPECTED_COMMITTEE_ROLES if role not in held]
