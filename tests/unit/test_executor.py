"""The in-process executor: the startup sweep, submission, and the daily budget."""

from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from deal_intel.contracts.agents.common import AgentName
from deal_intel.contracts.llm import LlmCallRecord, ModelRole, StopReason
from deal_intel.contracts.runs import AttemptStarted, RunErrorCode, RunState
from deal_intel.db.models import LlmCallRow
from deal_intel.db.writes import column_values
from deal_intel.orchestration.executor import (
    DailyBudgetExceeded,
    ExecutorNotStarted,
    RunExecutor,
)
from deal_intel.orchestration.persistence import lock_run, transition

FUTURE_TIMEOUT_SECONDS = 30
SPENT_CALL_ID = "spent-call"


def spent_call(created_at: datetime, cost: Decimal) -> LlmCallRow:
    record = LlmCallRecord(
        call_id=SPENT_CALL_ID,
        run_id=None,
        span_id=None,
        agent_name=AgentName.NEGOTIATION_STRATEGY.value,
        prompt_version="v1",
        prompt_hash="spent",
        input_hash="spent",
        model="stub-model",
        model_role=ModelRole.STRATEGY,
        turn=0,
        input_tokens=0,
        output_tokens=0,
        cache_creation_input_tokens=0,
        cache_read_input_tokens=0,
        cost_usd=cost,
        stop_reason=StopReason.END_TURN,
        latency_ms=0,
    )
    return LlmCallRow(**column_values(record), created_at=created_at)


def executor_for(run_bench, agents, settings=None) -> RunExecutor:
    settings = settings or run_bench.settings
    return RunExecutor(
        run_bench.runner(agents, settings), run_bench.session_factory, settings, run_bench.clock
    )


def test_submitted_run_completes_on_a_worker(run_bench) -> None:
    executor = executor_for(run_bench, run_bench.agents("OPP-1001"))
    executor.start()
    try:
        run_id = run_bench.create("USR-5001", "OPP-1001")
        record = executor.submit(run_id).result(timeout=FUTURE_TIMEOUT_SECONDS)
    finally:
        executor.shutdown()

    assert record.state is RunState.COMPLETED


def test_submit_before_start_is_refused(run_bench) -> None:
    executor = executor_for(run_bench, run_bench.agents("OPP-1001"))

    with pytest.raises(ExecutorNotStarted):
        executor.submit(run_bench.create("USR-5001", "OPP-1001"))


def test_startup_sweep_marks_working_and_queued_runs_interrupted(run_bench) -> None:
    queued = run_bench.create("USR-5001", "OPP-1001")
    working = run_bench.create("USR-5001", "OPP-1001")
    with run_bench.session_factory.begin() as session:
        row = lock_run(session, working)
        transition(
            session, row, RunState.ANALYZING, 1, AttemptStarted(attempt=1), run_bench.clock()
        )
    finished = run_bench.run(run_bench.agents("OPP-1001"), "USR-5001", "OPP-1001").run_id
    executor = executor_for(run_bench, run_bench.agents("OPP-1001"))

    interrupted = executor.start()
    executor.shutdown()

    assert sorted(interrupted) == sorted([queued, working])
    for run_id in (queued, working):
        assert run_bench.record(run_id).state is RunState.FAILED
        assert run_bench.events(run_id)[-1].detail == {
            "error_code": RunErrorCode.INTERRUPTED.value,
            "stage": None,
        }
    assert run_bench.record(finished).state is RunState.COMPLETED


def test_interrupted_run_resumes_from_its_unfinished_stages(run_bench) -> None:
    agents = run_bench.agents("OPP-1001")
    run_id = run_bench.create("USR-5001", "OPP-1001")
    executor = executor_for(run_bench, agents)
    executor.start()
    try:
        record = executor.submit(run_id).result(timeout=FUTURE_TIMEOUT_SECONDS)
    finally:
        executor.shutdown()

    assert record.state is RunState.COMPLETED
    assert [event.detail for event in run_bench.events(run_id)][:2] == [
        {"error_code": RunErrorCode.INTERRUPTED.value, "stage": None},
        {"attempt": 1},
    ]


def test_daily_budget_stops_new_submissions(run_bench) -> None:
    settings = run_bench.settings.model_copy(update={"daily_cost_budget_usd": 1.0})
    executor = executor_for(run_bench, run_bench.agents("OPP-1001"), settings)
    executor.start()
    with run_bench.session_factory.begin() as session:
        session.add(spent_call(run_bench.clock() - timedelta(hours=1), Decimal("1.50")))
    try:
        with pytest.raises(DailyBudgetExceeded):
            executor.submit(run_bench.create("USR-5001", "OPP-1001"))
        run_bench.clock.advance(timedelta(days=1))
        executor.require_budget()
    finally:
        executor.shutdown()
