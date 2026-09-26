from dataclasses import dataclass
from typing import Self

from deal_intel.config import Settings
from deal_intel.contracts.reference import Account, LowMediumHigh, Opportunity, PricingNote
from deal_intel.permissions.gate import is_restricted


@dataclass(frozen=True)
class SensitivityRule:
    """A pricing note is sensitive when its opportunity is restricted, it still needs approval,
    or its commercial risk is high. Values come from settings so Deal Desk can tighten them;
    a plain object lets tests pass literal values without a `.env`."""

    not_required_status: str
    high_risk_level: LowMediumHigh

    @classmethod
    def from_settings(cls, settings: Settings) -> Self:
        return cls(settings.pricing_not_required_status, settings.pricing_high_risk_level)

    def applies(self, note: PricingNote, opportunity: Opportunity, account: Account) -> bool:
        return (
            is_restricted(opportunity, account)
            or note.approval_status != self.not_required_status
            or note.commercial_risk == self.high_risk_level
        )
