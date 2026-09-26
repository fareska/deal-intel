from collections.abc import Sequence

from deal_intel.contracts.evidence import MAX_PACK_CHUNKS, EvidenceChunk, PackChunk
from deal_intel.retrieval.hashing import json_sha256
from deal_intel.retrieval.scoring import estimate_tokens


def to_pack_chunk(chunk: EvidenceChunk, score: float) -> PackChunk:
    return PackChunk(
        chunk_id=chunk.chunk_id,
        citation=chunk.citation(),
        kind=chunk.kind,
        source_type=chunk.source_type,
        opportunity_id=chunk.opportunity_id,
        account_id=chunk.account_id,
        access_level=chunk.access_level,
        event_date=chunk.event_date,
        author_or_speakers=chunk.author_or_speakers,
        text=chunk.text,
        content_hash=chunk.content_hash,
        score=score,
        estimated_tokens=estimate_tokens(chunk.text),
    )


def rank_key(chunk: PackChunk) -> tuple[float, str]:
    """Highest score first; the chunk id breaks ties so equal scores order the same every run."""
    return (-chunk.score, chunk.chunk_id)


def order_for_pack(baseline: Sequence[PackChunk], hits: Sequence[PackChunk]) -> list[PackChunk]:
    """Search hits form a tier above the baseline rather than joining one sort: `ts_rank_cd`
    values are usually below 0.1 while the baseline lexical factor is 1.0, so a single sort
    would bury every hit. A chunk matched by several queries keeps its best score."""
    best_hits: dict[str, PackChunk] = {}
    for hit in hits:
        current = best_hits.get(hit.chunk_id)
        if current is None or hit.score > current.score:
            best_hits[hit.chunk_id] = hit
    rest = [chunk for chunk in baseline if chunk.chunk_id not in best_hits]
    return sorted(best_hits.values(), key=rank_key) + sorted(rest, key=rank_key)


def fill_budget(ordered: Sequence[PackChunk], budget_tokens: int) -> tuple[list[PackChunk], bool]:
    """Takes chunks in order, skipping any that no longer fit and trying smaller ones after it.

    Returns the selection and whether anything was left out.
    """
    selected: list[PackChunk] = []
    used = 0
    for chunk in ordered:
        if used + chunk.estimated_tokens > budget_tokens or len(selected) == MAX_PACK_CHUNKS:
            continue
        selected.append(chunk)
        used += chunk.estimated_tokens
    return selected, len(selected) < len(ordered)


def hash_evidence(chunks: Sequence[PackChunk]) -> str:
    return json_sha256(sorted([chunk.chunk_id, chunk.content_hash] for chunk in chunks))
