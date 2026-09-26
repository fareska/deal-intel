from datetime import date
from enum import StrEnum
from typing import Self

from pydantic import Field, JsonValue, model_validator

from deal_intel.contracts.access import AccessLevel, SourceType
from deal_intel.contracts.base import StrictModel

CHUNK_ID_SEPARATOR = ":"
CHUNK_ID_PATTERN = r"^[a-z_]+:[A-Za-z0-9-]+(:\d+)?$"
MAX_PACK_CHUNKS = 200


class ChunkKind(StrEnum):
    SFDC_OPP = "sfdc_opp"
    SFDC_ACCOUNT = "sfdc_account"
    CONTACT = "contact"
    GONG_SUMMARY = "gong_summary"
    TRANSCRIPT = "transcript"
    PRICING = "pricing"
    POLICY = "policy"
    SLACK = "slack"


CHUNK_SOURCE_TYPES: dict[ChunkKind, SourceType] = {
    ChunkKind.SFDC_OPP: SourceType.SALESFORCE,
    ChunkKind.SFDC_ACCOUNT: SourceType.SALESFORCE,
    ChunkKind.CONTACT: SourceType.SALESFORCE,
    ChunkKind.GONG_SUMMARY: SourceType.GONG,
    ChunkKind.TRANSCRIPT: SourceType.GONG,
    ChunkKind.PRICING: SourceType.PRICING,
    ChunkKind.POLICY: SourceType.POLICIES,
    ChunkKind.SLACK: SourceType.SLACK,
}

CITATION_ID_FIELDS: dict[ChunkKind, str] = {
    ChunkKind.SFDC_OPP: "opportunity_id",
    ChunkKind.SFDC_ACCOUNT: "account_id",
    ChunkKind.CONTACT: "contact_id",
    ChunkKind.GONG_SUMMARY: "call_id",
    ChunkKind.TRANSCRIPT: "call_id",
    ChunkKind.PRICING: "pricing_note_id",
    ChunkKind.POLICY: "rule",
    ChunkKind.SLACK: "update_id",
}


class RetrievalOperation(StrEnum):
    LIST = "list"
    SEARCH = "search"
    GET = "get"


def make_chunk_id(kind: ChunkKind, source_key: str, segment: int | None = None) -> str:
    parts = [kind.value, source_key] if segment is None else [kind.value, source_key, str(segment)]
    return CHUNK_ID_SEPARATOR.join(parts)


def chunk_kind_of(chunk_id: str) -> ChunkKind:
    return ChunkKind(chunk_id.split(CHUNK_ID_SEPARATOR)[0])


def chunk_source_key_of(chunk_id: str) -> str:
    return chunk_id.split(CHUNK_ID_SEPARATOR)[1]


def chunk_segment_of(chunk_id: str) -> int | None:
    parts = chunk_id.split(CHUNK_ID_SEPARATOR)
    return int(parts[2]) if len(parts) == 3 else None


def format_citation(source_file: str, chunk_id: str, source_id: str) -> str:
    citation = f"source={source_file}, {CITATION_ID_FIELDS[chunk_kind_of(chunk_id)]}={source_id}"
    segment = chunk_segment_of(chunk_id)
    return citation if segment is None else f"{citation}, segment={segment}"


class EvidenceChunk(StrictModel):
    chunk_id: str = Field(pattern=CHUNK_ID_PATTERN)
    snapshot_id: str
    source_type: SourceType
    source_file: str
    source_id: str
    opportunity_id: str | None
    account_id: str | None
    access_level: AccessLevel
    event_date: date | None
    author_or_speakers: str | None
    text: str = Field(min_length=1)
    metadata: dict[str, JsonValue]
    content_hash: str

    @model_validator(mode="after")
    def require_source_type_of_kind(self) -> Self:
        expected = CHUNK_SOURCE_TYPES[self.kind]
        if expected != self.source_type:
            raise ValueError(f"{self.chunk_id} must have source_type {expected}")
        return self

    @property
    def kind(self) -> ChunkKind:
        return chunk_kind_of(self.chunk_id)

    @property
    def segment(self) -> int | None:
        return chunk_segment_of(self.chunk_id)

    def citation(self) -> str:
        return format_citation(self.source_file, self.chunk_id, self.source_id)


class PackChunk(StrictModel):
    """Repeats the access metadata so a pack can be re-checked against the scope on its own."""

    chunk_id: str = Field(pattern=CHUNK_ID_PATTERN)
    citation: str
    kind: ChunkKind
    source_type: SourceType
    opportunity_id: str | None
    account_id: str | None
    access_level: AccessLevel
    event_date: date | None
    author_or_speakers: str | None
    text: str
    content_hash: str
    score: float
    estimated_tokens: int = Field(ge=1)


class EvidencePack(StrictModel):
    agent_name: str
    opportunity_id: str
    snapshot_id: str
    budget_tokens: int = Field(ge=0)
    estimated_tokens: int = Field(ge=0)
    truncated: bool
    chunks: list[PackChunk] = Field(max_length=MAX_PACK_CHUNKS)

    def chunk_ids(self) -> frozenset[str]:
        return frozenset(chunk.chunk_id for chunk in self.chunks)

    def find(self, chunk_id: str) -> PackChunk | None:
        return next((chunk for chunk in self.chunks if chunk.chunk_id == chunk_id), None)


class RetrievalFilters(StrictModel):
    account_id: str
    opportunity_id: str
    requested_source_types: list[SourceType] | None
    effective_source_types: list[SourceType]
    permitted_levels: list[AccessLevel]
    sensitive_pricing_allowed: bool


class RetrievalRecord(StrictModel):
    operation: RetrievalOperation
    snapshot_id: str
    filters: RetrievalFilters
    query: str | None
    returned_ids: list[str]
    scores: dict[str, float]


class RetrievalResult(StrictModel):
    chunks: list[PackChunk]
    record: RetrievalRecord


class PackBuild(StrictModel):
    pack: EvidencePack
    records: list[RetrievalRecord]


class EvidenceHash(StrictModel):
    value: str
    record: RetrievalRecord
