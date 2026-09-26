from datetime import date, datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import Computed, DateTime, ForeignKey, Index, func
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from deal_intel.contracts.access import AccessLevel, SourceType
from deal_intel.contracts.evidence import EvidenceChunk
from deal_intel.db.base import Base
from deal_intel.db.models.checks import values_check

TEXT_SEARCH_CONFIG = "english"
EMBEDDING_DIMENSIONS = 1024
METADATA_COLUMN = "metadata"


class IngestSnapshotRow(Base):
    __tablename__ = "ingest_snapshots"

    snapshot_id: Mapped[str] = mapped_column(primary_key=True)
    content_hash: Mapped[str]
    file_manifest: Mapped[dict[str, str]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # clock_timestamp, not now(): two ingests inside one transaction must still be ordered.
    activated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.clock_timestamp()
    )


class EvidenceChunkRow(Base):
    __tablename__ = "evidence_chunks"
    __table_args__ = (
        values_check("source_type", SourceType, "ck_evidence_chunks_source_type"),
        values_check("access_level", AccessLevel, "ck_evidence_chunks_access_level"),
        Index(
            "ix_evidence_chunks_scope",
            "account_id",
            "opportunity_id",
            "source_type",
            "access_level",
        ),
        Index("ix_evidence_chunks_tsv", "tsv", postgresql_using="gin"),
    )

    chunk_id: Mapped[str] = mapped_column(primary_key=True)
    snapshot_id: Mapped[str] = mapped_column(ForeignKey("ingest_snapshots.snapshot_id"))
    source_type: Mapped[str]
    source_file: Mapped[str]
    source_id: Mapped[str]
    opportunity_id: Mapped[str | None]
    account_id: Mapped[str | None]
    access_level: Mapped[str]
    event_date: Mapped[date | None]
    author_or_speakers: Mapped[str | None]
    text: Mapped[str]
    # `metadata` is reserved on declarative classes (it is the table registry).
    metadata_: Mapped[dict[str, Any]] = mapped_column(METADATA_COLUMN, JSONB)
    content_hash: Mapped[str]
    tsv: Mapped[str] = mapped_column(
        TSVECTOR,
        Computed(f"to_tsvector('{TEXT_SEARCH_CONFIG}', text)", persisted=True),
        deferred=True,
    )
    embedding: Mapped[list[float] | None] = mapped_column(
        Vector(EMBEDDING_DIMENSIONS), deferred=True
    )


def chunk_from_row(row: EvidenceChunkRow) -> EvidenceChunk:
    return EvidenceChunk(
        chunk_id=row.chunk_id,
        snapshot_id=row.snapshot_id,
        source_type=SourceType(row.source_type),
        source_file=row.source_file,
        source_id=row.source_id,
        opportunity_id=row.opportunity_id,
        account_id=row.account_id,
        access_level=AccessLevel(row.access_level),
        event_date=row.event_date,
        author_or_speakers=row.author_or_speakers,
        text=row.text,
        metadata=row.metadata_,
        content_hash=row.content_hash,
    )
