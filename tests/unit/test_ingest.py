import shutil
from pathlib import Path

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from deal_intel.contracts.access import AccessLevel
from deal_intel.contracts.evidence import ChunkKind
from deal_intel.db.models import EvidenceChunkRow, IngestSnapshotRow
from deal_intel.retrieval.dataset import CITATION_ROOT, DatasetFile
from deal_intel.retrieval.ingest import IngestReport, latest_snapshot_id, load_evidence
from deal_intel.retrieval.reference import load_reference_data
from deal_intel.retrieval.sensitivity import SensitivityRule

EXPECTED_MINIMUM_COUNTS = {
    ChunkKind.SFDC_OPP: 3,
    ChunkKind.SFDC_ACCOUNT: 3,
    ChunkKind.CONTACT: 15,
    ChunkKind.GONG_SUMMARY: 27,
    ChunkKind.TRANSCRIPT: 9,
    ChunkKind.PRICING: 5,
    ChunkKind.POLICY: 10,
    ChunkKind.SLACK: 9,
}
EXPECTED_STORED_LEVELS = {
    "pricing:PN-4001": AccessLevel.STANDARD,
    "pricing:PN-4004": AccessLevel.SENSITIVE_PRICING,
    "slack:SLK-1003-02": AccessLevel.SENSITIVE_PRICING,
    "slack:SLK-1003-03": AccessLevel.RESTRICTED,
}


def load_all(session: Session, data_root: Path, sensitivity: SensitivityRule) -> IngestReport:
    load_reference_data(session, data_root)
    return load_evidence(session, data_root, sensitivity)


def changed_copy(dataset_root: Path, tmp_path: Path) -> Path:
    """A copy of the dataset with one byte appended to the policy file."""
    root = tmp_path / CITATION_ROOT
    shutil.copytree(dataset_root, root)
    with (root / DatasetFile.DEAL_DESK_POLICY).open("a", encoding="utf-8") as policy:
        policy.write("\n")
    return root


def row_count(session: Session, table: type) -> int:
    return session.scalar(select(func.count()).select_from(table)) or 0


def content_hashes(session: Session) -> set[str]:
    return set(session.scalars(select(EvidenceChunkRow.content_hash)))


def snapshot_ids_in_chunks(session: Session) -> set[str]:
    return set(session.scalars(select(EvidenceChunkRow.snapshot_id).distinct()))


def test_first_ingest_writes_every_kind(
    db_session: Session, dataset_root: Path, sensitivity: SensitivityRule
) -> None:
    report = load_all(db_session, dataset_root, sensitivity)

    assert not report.skipped
    for kind, minimum in EXPECTED_MINIMUM_COUNTS.items():
        assert report.chunk_counts[kind] >= minimum
    assert report.chunk_counts[ChunkKind.SLACK] == EXPECTED_MINIMUM_COUNTS[ChunkKind.SLACK]
    assert row_count(db_session, EvidenceChunkRow) == sum(report.chunk_counts.values())
    assert latest_snapshot_id(db_session) == report.snapshot_id


def test_second_ingest_is_a_no_op(
    db_session: Session, dataset_root: Path, sensitivity: SensitivityRule
) -> None:
    first = load_all(db_session, dataset_root, sensitivity)
    hashes_after_first = content_hashes(db_session)
    count_after_first = row_count(db_session, EvidenceChunkRow)

    second = load_all(db_session, dataset_root, sensitivity)

    assert second.skipped
    assert second.snapshot_id == first.snapshot_id
    assert second.chunk_counts == first.chunk_counts
    assert row_count(db_session, EvidenceChunkRow) == count_after_first
    assert content_hashes(db_session) == hashes_after_first
    assert row_count(db_session, IngestSnapshotRow) == 1


def test_full_text_search_finds_the_concession_note(ingested_session: Session) -> None:
    matches = ingested_session.scalars(
        select(EvidenceChunkRow.chunk_id).where(
            text("tsv @@ websearch_to_tsquery('english', :query)").bindparams(query="concession")
        )
    )

    assert "pricing:PN-4004" in set(matches)


def test_stored_access_levels(ingested_session: Session) -> None:
    rows = ingested_session.execute(
        select(EvidenceChunkRow.chunk_id, EvidenceChunkRow.access_level).where(
            EvidenceChunkRow.chunk_id.in_(list(EXPECTED_STORED_LEVELS))
        )
    )

    assert {chunk_id: level for chunk_id, level in rows} == EXPECTED_STORED_LEVELS


def test_changed_input_creates_a_new_snapshot(
    db_session: Session, dataset_root: Path, sensitivity: SensitivityRule, tmp_path: Path
) -> None:
    first = load_all(db_session, dataset_root, sensitivity)

    second = load_all(db_session, changed_copy(dataset_root, tmp_path), sensitivity)

    assert not second.skipped
    assert second.snapshot_id != first.snapshot_id
    assert snapshot_ids_in_chunks(db_session) == {second.snapshot_id}
    assert latest_snapshot_id(db_session) == second.snapshot_id


def test_reverting_input_reactivates_the_earlier_snapshot(
    db_session: Session, dataset_root: Path, sensitivity: SensitivityRule, tmp_path: Path
) -> None:
    first = load_all(db_session, dataset_root, sensitivity)
    load_all(db_session, changed_copy(dataset_root, tmp_path), sensitivity)

    reverted = load_all(db_session, dataset_root, sensitivity)

    assert not reverted.skipped
    assert reverted.snapshot_id == first.snapshot_id
    assert snapshot_ids_in_chunks(db_session) == {first.snapshot_id}
    assert latest_snapshot_id(db_session) == first.snapshot_id
    assert row_count(db_session, IngestSnapshotRow) == 2


def test_database_rejects_unknown_chunk_access_level(ingested_session: Session) -> None:
    with pytest.raises(IntegrityError, match="ck_evidence_chunks_access_level"):
        ingested_session.execute(
            text("UPDATE evidence_chunks SET access_level = 'secret' WHERE chunk_id = :chunk_id"),
            {"chunk_id": "policy:rule-1"},
        )
