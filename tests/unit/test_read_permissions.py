from deal_intel.contracts.access import AccessLevel
from deal_intel.permissions.read import brief_level_of, can_read_run
from tests.unit.api_harness import (
    DEAL_DESK,
    NARROW_READER,
    OPP_1001,
    OPP_1003,
    OUTSIDER,
    REQUESTER_1001,
    REQUESTER_1003,
)


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
