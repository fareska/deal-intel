from collections.abc import Iterable

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from deal_intel.contracts.access import AccessScope, Allowed, Denied, SourceType
from deal_intel.contracts.evidence import RetrievalOperation
from deal_intel.db.models import EvidenceChunkRow
from deal_intel.db.models.evidence import chunk_from_row
from deal_intel.permissions.gate import authorize, decide
from deal_intel.permissions.scope import chunk_is_in_scope, evidence_is_in_scope
from deal_intel.retrieval.reference import ReferenceData
from deal_intel.retrieval.retriever import NoSnapshot, ScopedRetriever

ALLOWED_PAIR_COUNT = 9
PACK_AGENT = "negotiation_strategy"
PRICING_QUERY = "discount concession procurement"
VERBAL_APPROVAL_QUERY = "verbally okayed discount"
VERBAL_APPROVAL_CHUNK = "slack:SLK-1003-02"
SMALL_BUDGET_TOKENS = 2_000
LARGE_BUDGET_TOKENS = 100_000
RANKING_DIAGNOSTIC_DEPTH = 15


def scope_for(session: Session, user_id: str, opportunity_id: str) -> AccessScope:
    result = authorize(session, user_id, opportunity_id)
    assert isinstance(result, Allowed)
    return result.scope


def retriever_for(session: Session, user_id: str, opportunity_id: str) -> ScopedRetriever:
    return ScopedRetriever(session, scope_for(session, user_id, opportunity_id))


def listed_ids(
    retriever: ScopedRetriever, source_types: Iterable[SourceType] | None = None
) -> list[str]:
    return retriever.list(source_types).record.returned_ids


def searched_ids(retriever: ScopedRetriever, query: str, k: int | None = None) -> list[str]:
    return retriever.search(query, k=k).record.returned_ids


def assert_list_matches_predicate(
    session: Session, scope: AccessScope, table: list[EvidenceChunkRow]
) -> None:
    retriever = ScopedRetriever(session, scope)
    listed = retriever.list().chunks
    expected = {
        row.chunk_id
        for row in table
        if evidence_is_in_scope(scope, retriever.snapshot_id, chunk_from_row(row))
    }

    assert all(chunk_is_in_scope(scope, chunk) for chunk in listed)
    assert {chunk.chunk_id for chunk in listed} == expected


def test_list_matches_the_python_predicate_for_every_allowed_pair(
    ingested_session: Session, reference: ReferenceData
) -> None:
    table = list(ingested_session.scalars(select(EvidenceChunkRow)))
    results = [
        authorize(ingested_session, user_id, opportunity_id)
        for user_id in reference.users
        for opportunity_id in reference.opportunities
    ]
    allowed = [result for result in results if isinstance(result, Allowed)]

    for result in allowed:
        assert_list_matches_predicate(ingested_session, result.scope, table)
    assert len(allowed) == ALLOWED_PAIR_COUNT


def test_narrow_scope_retrieves_no_slack_pricing_or_policies(ingested_session: Session) -> None:
    retriever = retriever_for(ingested_session, "USR-5007", "OPP-1001")

    source_types = {chunk.source_type for chunk in retriever.list().chunks}

    assert source_types == {SourceType.SALESFORCE, SourceType.GONG}
    assert listed_ids(retriever, [SourceType.PRICING]) == []
    assert retriever.search(VERBAL_APPROVAL_QUERY, [SourceType.SLACK]).chunks == []


def test_account_owner_sees_own_pricing_and_slack_only(ingested_session: Session) -> None:
    chunks = retriever_for(ingested_session, "USR-5001", "OPP-1001").list().chunks
    ids = {chunk.chunk_id for chunk in chunks}

    assert {"pricing:PN-4001", "pricing:PN-4002"} <= ids
    assert {"slack:SLK-1001-01", "slack:SLK-1001-02", "slack:SLK-1001-03"} <= ids
    assert {chunk.account_id for chunk in chunks} <= {"ACC-2001", None}
    assert {chunk.opportunity_id for chunk in chunks} <= {"OPP-1001", None}


def ranking_of(retriever: ScopedRetriever, query: str) -> tuple[list[str], str]:
    """Ranked ids plus a readable id=score list, so a failing ranking test shows the ranking."""
    record = retriever.search(query, k=RANKING_DIAGNOSTIC_DEPTH).record
    ranked = record.returned_ids
    shown = "; ".join(f"{chunk_id}={record.scores[chunk_id]:.5f}" for chunk_id in ranked)
    return ranked, shown


def test_concession_search_ranks_pricing_and_final_call(ingested_session: Session) -> None:
    retriever = retriever_for(ingested_session, "USR-5003", "OPP-1003")

    ranked, shown = ranking_of(retriever, PRICING_QUERY)

    assert "pricing:PN-4004" in ranked[:5], shown
    assert any(chunk_id.startswith("transcript:CALL-027:") for chunk_id in ranked[:10]), shown


def test_verbal_approval_claim_ranks_near_the_top(ingested_session: Session) -> None:
    retriever = retriever_for(ingested_session, "USR-5003", "OPP-1003")

    ranked, shown = ranking_of(retriever, VERBAL_APPROVAL_QUERY)

    assert VERBAL_APPROVAL_CHUNK in ranked[:3], shown


def test_denied_result_cannot_build_a_retriever(ingested_session: Session) -> None:
    denied = authorize(ingested_session, "USR-5004", "OPP-1003")

    assert isinstance(denied, Denied)
    with pytest.raises(TypeError):
        ScopedRetriever(ingested_session, denied)  # type: ignore[arg-type]


def test_allowed_wrapper_is_not_a_scope(ingested_session: Session) -> None:
    allowed = authorize(ingested_session, "USR-5001", "OPP-1001")

    with pytest.raises(TypeError):
        ScopedRetriever(ingested_session, allowed)  # type: ignore[arg-type]


def test_retriever_needs_an_ingested_snapshot(
    db_session: Session, reference: ReferenceData
) -> None:
    opportunity = reference.opportunities["OPP-1001"]
    account = reference.accounts[opportunity.account_id]
    allowed = decide(reference.users["USR-5001"], opportunity, account)

    assert isinstance(allowed, Allowed)
    with pytest.raises(NoSnapshot, match="deal-intel ingest"):
        ScopedRetriever(db_session, allowed.scope)


def test_small_budget_truncates_the_pack(ingested_session: Session) -> None:
    retriever = retriever_for(ingested_session, "USR-5003", "OPP-1003")

    build = retriever.build_pack(PACK_AGENT, SMALL_BUDGET_TOKENS, [PRICING_QUERY])

    assert build.pack.truncated
    assert build.pack.estimated_tokens <= SMALL_BUDGET_TOKENS
    assert build.pack.estimated_tokens == sum(chunk.estimated_tokens for chunk in build.pack.chunks)
    assert [record.operation for record in build.records] == [
        RetrievalOperation.LIST,
        RetrievalOperation.SEARCH,
    ]


def test_search_hits_lead_the_pack(ingested_session: Session) -> None:
    retriever = retriever_for(ingested_session, "USR-5003", "OPP-1003")
    top_hits = searched_ids(retriever, VERBAL_APPROVAL_QUERY)

    build = retriever.build_pack(PACK_AGENT, LARGE_BUDGET_TOKENS, [VERBAL_APPROVAL_QUERY])

    assert not build.pack.truncated
    assert [chunk.chunk_id for chunk in build.pack.chunks[: len(top_hits)]] == top_hits
    assert build.pack.chunk_ids() == frozenset(listed_ids(retriever))


def test_records_mirror_returned_chunks(ingested_session: Session) -> None:
    retriever = retriever_for(ingested_session, "USR-5003", "OPP-1003")

    result = retriever.search(PRICING_QUERY)

    assert result.record.returned_ids == [chunk.chunk_id for chunk in result.chunks]
    assert list(result.record.scores) == result.record.returned_ids
    assert result.record.query == PRICING_QUERY
    assert result.record.filters.account_id == "ACC-2003"
    assert result.record.snapshot_id == retriever.snapshot_id


def test_results_are_deterministic(ingested_session: Session) -> None:
    retriever = retriever_for(ingested_session, "USR-5005", "OPP-1003")

    assert listed_ids(retriever) == listed_ids(retriever)
    assert searched_ids(retriever, PRICING_QUERY) == searched_ids(retriever, PRICING_QUERY)


def test_evidence_hash_is_stable_and_scope_dependent(ingested_session: Session) -> None:
    owner = retriever_for(ingested_session, "USR-5001", "OPP-1001")
    narrow = retriever_for(ingested_session, "USR-5007", "OPP-1001")

    assert owner.evidence_hash().value == owner.evidence_hash().value
    assert owner.evidence_hash().value != narrow.evidence_hash().value


def test_slack_filter_lists_the_deal_updates_in_id_order(ingested_session: Session) -> None:
    retriever = retriever_for(ingested_session, "USR-5001", "OPP-1001")

    slack = listed_ids(retriever, [SourceType.SLACK])

    assert slack == ["slack:SLK-1001-01", "slack:SLK-1001-02", "slack:SLK-1001-03"]
