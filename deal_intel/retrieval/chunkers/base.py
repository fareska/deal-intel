from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import Enum
from pathlib import Path

from pydantic import JsonValue

from deal_intel.contracts.access import AccessLevel
from deal_intel.contracts.evidence import (
    CHUNK_SOURCE_TYPES,
    ChunkKind,
    EvidenceChunk,
    make_chunk_id,
)
from deal_intel.retrieval.dataset import citation_path
from deal_intel.retrieval.hashing import json_sha256
from deal_intel.retrieval.reference import ReferenceData
from deal_intel.retrieval.sensitivity import SensitivityRule

YES = "yes"
NO = "no"
LIST_SEPARATOR = ", "
LABEL_SEPARATOR = ": "


class SourceFormatError(ValueError):
    def __init__(self, path: Path, problem: str) -> None:
        super().__init__(f"{path}: {problem}")
        self.path = path


@dataclass(frozen=True)
class IngestContext:
    data_root: Path
    snapshot_id: str
    reference: ReferenceData
    sensitivity: SensitivityRule


@dataclass(frozen=True)
class ChunkDraft:
    """Everything a chunker decides; `make_chunk` fills in the derived fields."""

    kind: ChunkKind
    source_key: str
    relative_path: str
    source_id: str
    opportunity_id: str | None
    account_id: str | None
    access_level: AccessLevel
    text: str
    metadata: dict[str, JsonValue]
    event_date: date | None = None
    author_or_speakers: str | None = None
    segment: int | None = None


type Chunker = Callable[[IngestContext], list[EvidenceChunk]]


def make_chunk(context: IngestContext, draft: ChunkDraft) -> EvidenceChunk:
    fields = {
        "chunk_id": make_chunk_id(draft.kind, draft.source_key, draft.segment),
        "source_type": CHUNK_SOURCE_TYPES[draft.kind],
        "source_file": citation_path(draft.relative_path),
        "source_id": draft.source_id,
        "opportunity_id": draft.opportunity_id,
        "account_id": draft.account_id,
        "access_level": draft.access_level,
        "event_date": draft.event_date,
        "author_or_speakers": draft.author_or_speakers,
        "text": draft.text,
        "metadata": draft.metadata,
    }
    # The snapshot id is left out so unchanged content keeps its hash across snapshots.
    return EvidenceChunk(
        **fields, snapshot_id=context.snapshot_id, content_hash=json_sha256(fields)
    )


def labelled_text(heading: str, fields: Sequence[tuple[str, object]]) -> str:
    """Labels let a query such as "close date" hit the right line and tell the model what a
    bare number means."""
    return "\n".join(
        [heading, *(f"{label}{LABEL_SEPARATOR}{format_value(value)}" for label, value in fields)]
    )


def format_value(value: object) -> str:
    if isinstance(value, bool):
        return YES if value else NO
    if isinstance(value, Enum):
        return str(value.value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, list | tuple):
        return LIST_SEPARATOR.join(format_value(item) for item in value)
    return str(value)


def plain_number(value: Decimal | int) -> str:
    """`18`, not `18.00` or `1.8E+1`, whatever scale the value was stored with."""
    return f"{Decimal(value).normalize():f}"


def percent(value: Decimal | int) -> str:
    return f"{plain_number(value)}%"
