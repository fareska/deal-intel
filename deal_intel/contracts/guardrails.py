from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated

from pydantic import AfterValidator, Field

from deal_intel.contracts.base import StrictModel
from deal_intel.contracts.evidence import CHUNK_ID_PATTERN, PackChunk
from deal_intel.contracts.reference import LowMediumHigh

MAX_EVIDENCE_IDS = 12
OUTPUT_ITEM_REF = "output"

ChunkId = Annotated[str, Field(pattern=CHUNK_ID_PATTERN)]

Confidence = LowMediumHigh


def require_unique[ItemT](values: list[ItemT]) -> list[ItemT]:
    if len(set(values)) != len(values):
        raise ValueError("list must not repeat a value")
    return values


EvidenceIds = Annotated[
    list[ChunkId],
    Field(min_length=1, max_length=MAX_EVIDENCE_IDS),
    AfterValidator(require_unique),
]


class EvidenceBacked(StrictModel):
    """An output item resting on the chunks it cites; the validators act on these.
    `confidence` states how directly that evidence supports the item."""

    evidence_ids: EvidenceIds
    confidence: Confidence


class GuardrailCheck(StrEnum):
    SCHEMA = "schema"
    BOUNDS = "bounds"
    CITATIONS = "citations"
    NUMBERS = "numbers"
    QUOTES = "quotes"
    NAMES = "names"
    APPROVAL_WORDING = "approval_wording"
    CUSTOMER_FACING_LEAK = "customer_facing_leak"
    DEGRADED_INPUTS = "degraded_inputs"
    RETRY = "retry"
    LANGUAGE_LINT = "language_lint"
    APPROVAL_CONSISTENCY = "approval_consistency"
    LEAKAGE_CANARIES = "leakage_canaries"


class GuardrailOutcome(StrEnum):
    PASSED = "passed"
    DROPPED = "dropped"
    MODIFIED = "modified"
    WARNING = "warning"
    RETRIED = "retried"


class GuardrailResult(StrictModel):
    check: GuardrailCheck
    outcome: GuardrailOutcome
    item_ref: str
    detail: str = ""


@dataclass(frozen=True)
class GuardrailReport[OutputT]:
    """`output` is already cleaned: offending items dropped or modified. `feedback` holds one
    message per problem, worded for the model, so the retry policy can send it back."""

    output: OutputT
    results: tuple[GuardrailResult, ...]
    feedback: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return not self.feedback


type OutputCheck[OutputT] = Callable[[OutputT, Sequence[PackChunk]], GuardrailReport[OutputT]]
"""Checks one parsed output. The chunks are those tools returned during the call, which count as
evidence alongside the pack the check was built with."""
