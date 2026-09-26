"""The deterministic deal snapshot: reference rows copied verbatim, never through a model.

Which pricing notes appear is decided only by the scoped retriever, so no permission logic is
repeated here. This module must never import the LLM package; a test enforces it.
"""

from collections.abc import Sequence

from sqlalchemy.orm import Session

from deal_intel.contracts.access import SourceType
from deal_intel.contracts.agents.deal_snapshot import (
    CitedRecord,
    DealSnapshot,
    SnapshotBuild,
    visibility_of,
)
from deal_intel.contracts.base import StrictModel
from deal_intel.contracts.evidence import ChunkKind, PackChunk, chunk_source_key_of, make_chunk_id
from deal_intel.contracts.reference import PricingNote
from deal_intel.db.models import PricingNoteRow
from deal_intel.permissions.lookups import find_account, find_contract, find_opportunity
from deal_intel.retrieval.retriever import ScopedRetriever

SNAPSHOT_SOURCE_TYPES: tuple[SourceType, ...] = (SourceType.SALESFORCE, SourceType.PRICING)


class SnapshotInputMissing(LookupError):
    """Only reachable when a re-ingest removed rows the scope still points at."""


def build_deal_snapshot(session: Session, retriever: ScopedRetriever) -> SnapshotBuild:
    scope = retriever.scope
    listed = retriever.list(SNAPSHOT_SOURCE_TYPES)
    opportunity = find_opportunity(session, scope.opportunity_id)
    account = find_account(session, scope.account_id)
    if opportunity is None or account is None:
        raise SnapshotInputMissing("opportunity or account row is missing")
    opportunity_chunk = find_chunk(listed.chunks, ChunkKind.SFDC_OPP, scope.opportunity_id)
    account_chunk = find_chunk(listed.chunks, ChunkKind.SFDC_ACCOUNT, scope.account_id)
    pricing_notes = cite_pricing_notes(session, listed.chunks)
    snapshot = DealSnapshot(
        opportunity=cite(opportunity, opportunity_chunk),
        account=cite(account, account_chunk),
        pricing_notes=pricing_notes,
        pricing_visibility=visibility_of(pricing_notes),
    )
    return SnapshotBuild(snapshot=snapshot, records=[listed.record])


def find_chunk(chunks: Sequence[PackChunk], kind: ChunkKind, source_key: str) -> PackChunk:
    chunk_id = make_chunk_id(kind, source_key)
    chunk = next((chunk for chunk in chunks if chunk.chunk_id == chunk_id), None)
    if chunk is None:
        raise SnapshotInputMissing(f"{chunk_id} is not in the retrieved evidence")
    return chunk


def cite_pricing_notes(
    session: Session, chunks: Sequence[PackChunk]
) -> list[CitedRecord[PricingNote]]:
    pricing_chunks = sorted(
        (chunk for chunk in chunks if chunk.kind == ChunkKind.PRICING),
        key=lambda chunk: chunk.chunk_id,
    )
    return [cite(load_pricing_note(session, chunk), chunk) for chunk in pricing_chunks]


def load_pricing_note(session: Session, chunk: PackChunk) -> PricingNote:
    note_id = chunk_source_key_of(chunk.chunk_id)
    note = find_contract(session, PricingNoteRow, PricingNote, note_id)
    if note is None:
        raise SnapshotInputMissing(f"pricing note {note_id} is missing")
    return note


def cite[RecordT: StrictModel](record: RecordT, chunk: PackChunk) -> CitedRecord[RecordT]:
    return CitedRecord(record=record, evidence_id=chunk.chunk_id, citation=chunk.citation)
