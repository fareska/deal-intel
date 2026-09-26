import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import Self

from deal_intel.config import Settings
from deal_intel.contracts.evidence import ChunkKind

# recency = 1 / (1 + days / RECENCY_SCALE_DAYS): evidence this many days older than the
# reference date counts half as recent.
RECENCY_SCALE_DAYS = 90
# score = lexical * reliability * (RECENCY_FLOOR + (1 - RECENCY_FLOOR) * recency), so age can
# at most halve a score and never hides old evidence.
RECENCY_FLOOR = 0.5
UNDATED_RECENCY = 1.0
CHARS_PER_TOKEN = 4
BASELINE_LEXICAL = 1.0


@dataclass(frozen=True)
class ScoringConfig:
    reliability_weights: Mapping[ChunkKind, float]
    search_k: int

    @classmethod
    def from_settings(cls, settings: Settings) -> Self:
        return cls(dict(settings.reliability_weights), settings.search_k)


def recency(event_date: date | None, reference_date: date | None) -> float:
    """Salesforce rows, pricing notes, and policy rules are undated facts that do not age."""
    if event_date is None or reference_date is None:
        return UNDATED_RECENCY
    days = max((reference_date - event_date).days, 0)
    return 1 / (1 + days / RECENCY_SCALE_DAYS)


def final_score(
    lexical: float, reliability: float, event_date: date | None, reference_date: date | None
) -> float:
    age_factor = RECENCY_FLOOR + (1 - RECENCY_FLOOR) * recency(event_date, reference_date)
    return lexical * reliability * age_factor


def estimate_tokens(text: str) -> int:
    """Only fills a budget; real usage comes from the model API response."""
    return max(1, math.ceil(len(text) / CHARS_PER_TOKEN))
