import pytest

from deal_intel.evaluation.scenarios import EVAL_PAIRS, DemoPair
from tests.unit.conftest import OPP_1003, STUB_OUTPUTS


@pytest.fixture
def eval_runs(run_bench) -> list[tuple[DemoPair, str]]:
    completed: list[tuple[DemoPair, str]] = []
    for pair in EVAL_PAIRS:
        opportunity = pair.opportunity_id if pair.opportunity_id in STUB_OUTPUTS else OPP_1003
        record = run_bench.run(run_bench.agents(opportunity), pair.user_id, pair.opportunity_id)
        completed.append((pair, record.run_id))
    return completed
