from collections.abc import Sequence
from enum import StrEnum
from typing import Any, Self

from pydantic import Field, model_validator

from deal_intel.contracts.base import StrictModel
from deal_intel.contracts.evidence import RetrievalRecord
from deal_intel.contracts.guardrails import ChunkId
from deal_intel.contracts.reference import Account, Opportunity, PricingNote

MAX_SNAPSHOT_PRICING_NOTES = 20


class PricingVisibility(StrEnum):
    """No `partial`: it would tell the reader that notes exist which they may not see."""

    VISIBLE = "visible"
    NONE = "none"


class CitedRecord[RecordT: StrictModel](StrictModel):
    """A reference row copied verbatim, with the evidence chunk that cites it."""

    record: RecordT
    evidence_id: ChunkId
    citation: str = Field(min_length=1)


def visibility_of(pricing_notes: Sequence[CitedRecord[PricingNote]]) -> PricingVisibility:
    return PricingVisibility.VISIBLE if pricing_notes else PricingVisibility.NONE


class DealSnapshot(StrictModel):
    opportunity: CitedRecord[Opportunity]
    account: CitedRecord[Account]
    pricing_notes: list[CitedRecord[PricingNote]] = Field(max_length=MAX_SNAPSHOT_PRICING_NOTES)
    pricing_visibility: PricingVisibility

    @model_validator(mode="after")
    def require_one_deal(self) -> Self:
        opportunity = self.opportunity.record
        if self.account.record.account_id != opportunity.account_id:
            raise ValueError("account does not belong to the opportunity")
        if any(
            note.record.opportunity_id != opportunity.opportunity_id for note in self.pricing_notes
        ):
            raise ValueError("every pricing note must belong to the opportunity")
        if self.pricing_visibility != visibility_of(self.pricing_notes):
            raise ValueError("pricing_visibility must be visible exactly when notes are present")
        return self

    def cited_blocks(self) -> list[CitedRecord[Any]]:
        """Opportunity, account, then pricing notes: the order the brief cites them in."""
        return [self.opportunity, self.account, *self.pricing_notes]

    def evidence_ids(self) -> list[str]:
        return [block.evidence_id for block in self.cited_blocks()]

    def citations(self) -> list[str]:
        return [block.citation for block in self.cited_blocks()]


class SnapshotBuild(StrictModel):
    snapshot: DealSnapshot
    records: list[RetrievalRecord]
