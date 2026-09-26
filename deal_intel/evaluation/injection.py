"""Poisoned evidence chunks used by the safety suite and `scripts/record_fixtures.py --inject`."""

from pathlib import Path

from pydantic import TypeAdapter
from sqlalchemy.orm import Session

from deal_intel.contracts.base import StrictModel
from deal_intel.contracts.evidence import PackBuild, PackChunk, chunk_source_key_of
from deal_intel.db.models import EvidenceChunkRow
from deal_intel.evaluation.scenarios import INJECTION_ROOT
from deal_intel.retrieval.hashing import sha256_hex
from deal_intel.retrieval.ingest import latest_snapshot_id

INJECTION_SOURCE_FILE = "tests/fixtures/injection"
INJECTION_REVIEW_NOTE = "Evidence contained an embedded instruction; it was treated as data."
APPROVED_WORD = "approved"
INJECTION_FIXTURE_SUFFIX = ".json"


class InjectionFixture(StrictModel):
    chunk: PackChunk
    forbidden_phrases: list[str]


INJECTION_FIXTURES = TypeAdapter(list[InjectionFixture])


def injection_paths(root: Path = INJECTION_ROOT) -> list[Path]:
    return sorted(root.glob(f"*{INJECTION_FIXTURE_SUFFIX}"))


def load_injection(path: Path) -> InjectionFixture:
    return InjectionFixture.model_validate_json(path.read_bytes())


def load_injections(root: Path = INJECTION_ROOT) -> list[InjectionFixture]:
    return [load_injection(path) for path in injection_paths(root)]


def with_injected_chunk(pack_build: PackBuild, chunk: PackChunk) -> PackBuild:
    if pack_build.pack.find(chunk.chunk_id) is not None:
        return pack_build
    pack = pack_build.pack.model_copy(
        update={
            "chunks": [*pack_build.pack.chunks, chunk],
            "estimated_tokens": pack_build.pack.estimated_tokens + chunk.estimated_tokens,
        }
    )
    return pack_build.model_copy(update={"pack": pack})


def persist_injection_chunk(session: Session, chunk: PackChunk) -> None:
    snapshot_id = latest_snapshot_id(session)
    if snapshot_id is None:
        raise RuntimeError("no evidence snapshot exists; ingest before injecting a chunk")
    session.merge(
        EvidenceChunkRow(
            chunk_id=chunk.chunk_id,
            snapshot_id=snapshot_id,
            source_type=chunk.source_type.value,
            source_file=INJECTION_SOURCE_FILE,
            source_id=chunk_source_key_of(chunk.chunk_id),
            opportunity_id=chunk.opportunity_id,
            account_id=chunk.account_id,
            access_level=chunk.access_level.value,
            event_date=chunk.event_date,
            author_or_speakers=chunk.author_or_speakers,
            text=chunk.text,
            metadata_={},
            content_hash=chunk.content_hash or sha256_hex(chunk.text.encode("utf-8")),
        )
    )
    session.flush()
