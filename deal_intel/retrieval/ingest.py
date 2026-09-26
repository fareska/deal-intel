from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from deal_intel.config import get_settings
from deal_intel.contracts.evidence import ChunkKind, EvidenceChunk
from deal_intel.db.models import EvidenceChunkRow, IngestSnapshotRow
from deal_intel.db.writes import column_values, upsert_rows
from deal_intel.retrieval.chunkers import CHUNKERS
from deal_intel.retrieval.chunkers.base import IngestContext
from deal_intel.retrieval.dataset import (
    EVIDENCE_FILES,
    OPTIONAL_EVIDENCE_FILES,
    relative_to_root,
    transcript_paths,
)
from deal_intel.retrieval.hashing import json_sha256, sha256_hex
from deal_intel.retrieval.reference import read_reference_data
from deal_intel.retrieval.sensitivity import SensitivityRule

SNAPSHOT_ID_LENGTH = 16


class DuplicateChunkId(ValueError):
    pass


@dataclass(frozen=True)
class IngestReport:
    snapshot_id: str
    skipped: bool
    chunk_counts: dict[ChunkKind, int]


def load_evidence(
    session: Session, data_root: Path, sensitivity: SensitivityRule | None = None
) -> IngestReport:
    """Mirrors the input files into `evidence_chunks`; unchanged inputs write nothing.

    The caller owns the transaction, so reference and evidence loads commit or fail together.
    """
    manifest = build_manifest(data_root)
    content_hash = json_sha256(manifest)
    snapshot_id = content_hash[:SNAPSHOT_ID_LENGTH]
    chunks = build_chunks(build_context(data_root, snapshot_id, sensitivity))
    skipped = latest_snapshot_id(session) == snapshot_id
    if not skipped:
        activate_snapshot(session, snapshot_id, content_hash, manifest)
        replace_chunks(session, chunks)
    return IngestReport(snapshot_id, skipped, count_by_kind(chunks))


def build_manifest(data_root: Path) -> dict[str, str]:
    """Relative path to content hash for every evidence input; any changed byte changes it."""
    paths = [
        data_root / dataset_file
        for dataset_file in EVIDENCE_FILES
        if dataset_file not in OPTIONAL_EVIDENCE_FILES or (data_root / dataset_file).exists()
    ]
    paths.extend(transcript_paths(data_root))
    return {relative_to_root(data_root, path): sha256_hex(path.read_bytes()) for path in paths}


def build_context(
    data_root: Path, snapshot_id: str, sensitivity: SensitivityRule | None = None
) -> IngestContext:
    return IngestContext(
        data_root=data_root,
        snapshot_id=snapshot_id,
        reference=read_reference_data(data_root),
        sensitivity=sensitivity or SensitivityRule.from_settings(get_settings()),
    )


def build_chunks(context: IngestContext) -> list[EvidenceChunk]:
    chunks = [chunk for chunker in CHUNKERS for chunk in chunker(context)]
    duplicates = sorted(
        chunk_id
        for chunk_id, count in Counter(chunk.chunk_id for chunk in chunks).items()
        if count > 1
    )
    if duplicates:
        raise DuplicateChunkId(f"chunk ids produced more than once: {duplicates}")
    return chunks


def latest_snapshot_id(session: Session) -> str | None:
    return session.scalar(
        select(IngestSnapshotRow.snapshot_id)
        .order_by(IngestSnapshotRow.activated_at.desc(), IngestSnapshotRow.snapshot_id)
        .limit(1)
    )


def activate_snapshot(
    session: Session, snapshot_id: str, content_hash: str, manifest: dict[str, str]
) -> None:
    """Re-activating an earlier snapshot (inputs reverted) refreshes it instead of duplicating."""
    statement = insert(IngestSnapshotRow).values(
        snapshot_id=snapshot_id, content_hash=content_hash, file_manifest=manifest
    )
    session.execute(
        statement.on_conflict_do_update(
            index_elements=[IngestSnapshotRow.snapshot_id],
            set_={"activated_at": func.clock_timestamp()},
        )
    )


def replace_chunks(session: Session, chunks: list[EvidenceChunk]) -> None:
    """The table mirrors the current inputs; `ingest_snapshots` keeps the earlier manifests."""
    upsert_rows(session, EvidenceChunkRow, [column_values(chunk) for chunk in chunks])
    current_ids = [chunk.chunk_id for chunk in chunks]
    session.execute(delete(EvidenceChunkRow).where(EvidenceChunkRow.chunk_id.not_in(current_ids)))


def count_by_kind(chunks: list[EvidenceChunk]) -> dict[ChunkKind, int]:
    counts = Counter(chunk.kind for chunk in chunks)
    return {kind: counts[kind] for kind in ChunkKind}
