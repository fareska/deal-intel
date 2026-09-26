import pytest
from sqlalchemy import select

from deal_intel.contracts.access import AccessLevel, Allowed
from deal_intel.db.models.evidence import EvidenceChunkRow, chunk_from_row
from deal_intel.orchestration.outputs import stored_analysis
from deal_intel.orchestration.persistence import successful_outputs
from deal_intel.permissions.gate import authorize
from deal_intel.permissions.read import brief_level_of, can_read_run
from deal_intel.permissions.scope import chunk_is_in_scope
from deal_intel.rendering.evidence import cited_chunk_ids
from tests.unit.api_harness import (
    DEAL_DESK,
    NARROW_READER,
    OPP_1001,
    OPP_1003,
    OUTSIDER,
    REQUESTER_1001,
    REQUESTER_1003,
)

RUNS_UNDER_TEST = [(OPP_1001, REQUESTER_1001), (OPP_1003, REQUESTER_1003)]
SECOND_READERS = [DEAL_DESK, OUTSIDER, NARROW_READER]


def test_requester_can_read_their_run(run_bench) -> None:
    record = run_bench.run(run_bench.agents(OPP_1001), REQUESTER_1001, OPP_1001)

    with run_bench.session_factory() as session:
        assert can_read_run(session, REQUESTER_1001, record)


def test_deal_desk_can_read_an_eclipse_run_at_the_brief_level(run_bench) -> None:
    record = run_bench.run(run_bench.agents(OPP_1003), REQUESTER_1003, OPP_1003)

    with run_bench.session_factory() as session:
        assert can_read_run(session, DEAL_DESK, record)
        assert brief_level_of(session, record) >= AccessLevel.RESTRICTED


def test_outsider_cannot_read_an_eclipse_run(run_bench) -> None:
    record = run_bench.run(run_bench.agents(OPP_1003), REQUESTER_1003, OPP_1003)

    with run_bench.session_factory() as session:
        assert not can_read_run(session, OUTSIDER, record)


def test_narrow_reader_cannot_read_a_pricing_brief(run_bench) -> None:
    record = run_bench.run(run_bench.agents(OPP_1003), REQUESTER_1003, OPP_1003)

    with run_bench.session_factory() as session:
        assert not can_read_run(session, NARROW_READER, record)
        assert brief_level_of(session, record) > AccessLevel.STANDARD


def test_narrow_reader_cannot_read_a_brief_citing_sources_outside_their_scope(run_bench) -> None:
    record = run_bench.run(run_bench.agents(OPP_1001), REQUESTER_1001, OPP_1001)

    with run_bench.session_factory() as session:
        assert not can_read_run(session, NARROW_READER, record)


@pytest.mark.parametrize(("opportunity_id", "requester"), RUNS_UNDER_TEST)
@pytest.mark.parametrize("reader", SECOND_READERS)
def test_second_reader_access_equals_scope_over_every_cited_chunk(
    run_bench, opportunity_id: str, requester: str, reader: str
) -> None:
    record = run_bench.run(run_bench.agents(opportunity_id), requester, opportunity_id)

    with run_bench.session_factory() as session:
        assert can_read_run(session, reader, record) == scope_covers_cited_chunks(
            session, reader, record
        )


def test_a_run_without_a_brief_is_hidden_from_second_readers(run_bench) -> None:
    record = run_bench.record(run_bench.create(REQUESTER_1003, OPP_1003))

    with run_bench.session_factory() as session:
        assert not can_read_run(session, DEAL_DESK, record)
        assert can_read_run(session, REQUESTER_1003, record)


def scope_covers_cited_chunks(session, reader: str, record) -> bool:
    """An independent oracle: the Python scope mirror, not the retriever's SQL."""
    decision = authorize(session, reader, record.opportunity_id)
    if not isinstance(decision, Allowed):
        return False
    chunk_ids = set(cited_chunk_ids(stored_analysis(successful_outputs(session, record.run_id))))
    rows = list(
        session.scalars(
            select(EvidenceChunkRow).where(
                EvidenceChunkRow.chunk_id.in_(chunk_ids),
                EvidenceChunkRow.snapshot_id == record.snapshot_id,
            )
        )
    )
    assert len(rows) == len(chunk_ids)
    return all(chunk_is_in_scope(decision.scope, chunk_from_row(row)) for row in rows)
