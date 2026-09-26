from datetime import date

import pytest

from deal_intel.contracts.access import AccessLevel, SourceType
from deal_intel.contracts.evidence import MAX_PACK_CHUNKS, ChunkKind, PackChunk
from deal_intel.retrieval.packing import fill_budget, hash_evidence, order_for_pack
from deal_intel.retrieval.scoring import estimate_tokens, final_score, recency

REFERENCE_DATE = date(2026, 4, 27)


def pack_chunk(chunk_id: str, score: float, estimated_tokens: int = 10) -> PackChunk:
    return PackChunk(
        chunk_id=chunk_id,
        citation=f"source=synthetic_data/test.tsv, id={chunk_id}",
        kind=ChunkKind.SLACK,
        source_type=SourceType.SLACK,
        opportunity_id="OPP-1001",
        account_id="ACC-2001",
        access_level=AccessLevel.STANDARD,
        event_date=None,
        author_or_speakers=None,
        text="text",
        content_hash=f"hash-{chunk_id}",
        score=score,
        estimated_tokens=estimated_tokens,
    )


def ids(chunks: list[PackChunk]) -> list[str]:
    return [chunk.chunk_id for chunk in chunks]


def test_recency_halves_after_ninety_days() -> None:
    assert recency(date(2026, 1, 27), REFERENCE_DATE) == pytest.approx(0.5)
    assert recency(REFERENCE_DATE, REFERENCE_DATE) == 1.0


def test_undated_and_future_evidence_does_not_age() -> None:
    assert recency(None, REFERENCE_DATE) == 1.0
    assert recency(REFERENCE_DATE, None) == 1.0
    assert recency(date(2026, 5, 1), REFERENCE_DATE) == 1.0


def test_final_score_applies_reliability_and_age() -> None:
    assert final_score(0.2, 0.7, date(2026, 1, 27), REFERENCE_DATE) == pytest.approx(0.105)
    assert final_score(1.0, 1.0, None, REFERENCE_DATE) == 1.0


def test_token_estimate() -> None:
    assert estimate_tokens("abcd" * 10) == 10
    assert estimate_tokens("abcde") == 2
    assert estimate_tokens("") == 1


def test_search_hits_rank_above_baseline_and_keep_their_best_score() -> None:
    baseline = [pack_chunk("slack:b", 1.0), pack_chunk("slack:a", 1.0), pack_chunk("slack:c", 1.0)]
    hits = [pack_chunk("slack:c", 0.02), pack_chunk("slack:d", 0.05), pack_chunk("slack:c", 0.09)]

    ordered = order_for_pack(baseline, hits)

    assert ids(ordered) == ["slack:c", "slack:d", "slack:a", "slack:b"]
    assert ordered[0].score == 0.09


def test_budget_skips_what_does_not_fit_and_keeps_smaller_chunks() -> None:
    ordered = [
        pack_chunk("slack:a", 1.0, 6),
        pack_chunk("slack:b", 0.9, 6),
        pack_chunk("slack:c", 0.8, 3),
    ]

    selected, truncated = fill_budget(ordered, 10)

    assert ids(selected) == ["slack:a", "slack:c"]
    assert truncated


def test_budget_that_fits_everything_is_not_truncated() -> None:
    ordered = [pack_chunk("slack:a", 1.0, 4), pack_chunk("slack:b", 0.9, 4)]

    selected, truncated = fill_budget(ordered, 8)

    assert ids(selected) == ["slack:a", "slack:b"]
    assert not truncated


def test_budget_respects_the_pack_size_limit() -> None:
    ordered = [pack_chunk(f"slack:{index}", 1.0, 1) for index in range(MAX_PACK_CHUNKS + 5)]

    selected, truncated = fill_budget(ordered, 10_000)

    assert len(selected) == MAX_PACK_CHUNKS
    assert truncated


def test_evidence_hash_ignores_order_and_scores() -> None:
    first = [pack_chunk("slack:a", 1.0), pack_chunk("slack:b", 0.5)]
    second = [pack_chunk("slack:b", 0.1), pack_chunk("slack:a", 0.2)]

    assert hash_evidence(first) == hash_evidence(second)
    assert hash_evidence(first) != hash_evidence(first[:1])
