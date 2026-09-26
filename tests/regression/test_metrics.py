"""Committed evaluation baseline and the metrics table."""

import os

import pytest

from deal_intel.config import get_settings
from deal_intel.evaluation.metrics import metrics_report
from deal_intel.evaluation.report import (
    compare_to_baseline,
    format_table,
    report_from_json,
    report_to_json,
)
from deal_intel.evaluation.scenarios import EVAL_BASELINE_PATH, EVAL_PAIRS, DemoPair
from tests.regression.test_goldens import WRITE_EVAL_ARTIFACTS


def test_metrics_stay_within_baseline(run_bench, eval_runs: list[tuple[DemoPair, str]]) -> None:
    with run_bench.session_factory() as session:
        report = metrics_report(session, eval_runs)
    path = EVAL_BASELINE_PATH
    if os.environ.get(WRITE_EVAL_ARTIFACTS) == "1":
        path.write_bytes(report_to_json(report))
    if not path.is_file():
        pytest.fail(f"missing baseline {path}; set {WRITE_EVAL_ARTIFACTS}=1 to write it")
    baseline = report_from_json(path.read_bytes())
    problems = compare_to_baseline(report, baseline, get_settings().eval_tolerance)
    assert not problems, "\n".join(problems)


def test_evaluate_table_lists_every_metric(
    run_bench, eval_runs: list[tuple[DemoPair, str]]
) -> None:
    with run_bench.session_factory() as session:
        report = metrics_report(session, eval_runs)
    table = format_table(report)

    for name in (
        "citation_validity_rate",
        "grounded_number_rate",
        "section_completeness",
        "approval_routing_accuracy",
        "denial_correctness",
        "degraded_rate",
        "mean_cost_usd",
        "mean_input_tokens",
        "mean_output_tokens",
        "guardrail_drops_by_check",
    ):
        assert name in table
    assert str(len(EVAL_PAIRS)) in table
