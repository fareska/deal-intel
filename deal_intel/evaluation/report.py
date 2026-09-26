from collections.abc import Mapping
from decimal import Decimal

from deal_intel.contracts.base import StrictModel

RATE_FIELDS: tuple[str, ...] = (
    "citation_validity_rate",
    "grounded_number_rate",
    "section_completeness",
    "approval_routing_accuracy",
    "denial_correctness",
    "degraded_rate",
)
PERFECT_FIELDS: tuple[str, ...] = ("approval_routing_accuracy", "denial_correctness")
TABLE_COLUMNS: tuple[str, ...] = ("metric", "value")
RATE_FORMAT = "{:.3f}"
COST_FORMAT = "{:.4f}"
COLUMN_GAP = "  "


class MetricsReport(StrictModel):
    citation_validity_rate: float
    grounded_number_rate: float
    section_completeness: float
    approval_routing_accuracy: float
    denial_correctness: float
    degraded_rate: float
    mean_cost_usd: Decimal
    mean_input_tokens: int
    mean_output_tokens: int
    guardrail_drops_by_check: dict[str, int]
    scenarios: int


def format_table(report: MetricsReport, extra: Mapping[str, str] | None = None) -> str:
    extras = tuple((extra or {}).items())
    rows = [("metric", "value"), *metric_rows(report), *extras]
    widths = [max(len(row[index]) for row in rows) for index in range(len(TABLE_COLUMNS))]

    def format_row(row: tuple[str, str]) -> str:
        return COLUMN_GAP.join(cell.ljust(widths[index]) for index, cell in enumerate(row))

    return "\n".join(format_row(row) for row in rows)


def metric_rows(report: MetricsReport) -> list[tuple[str, str]]:
    dumped = report.model_dump()
    rows: list[tuple[str, str]] = []
    for name in RATE_FIELDS:
        rows.append((name, RATE_FORMAT.format(dumped[name])))
    rows.append(("mean_cost_usd", COST_FORMAT.format(report.mean_cost_usd)))
    rows.append(("mean_input_tokens", str(report.mean_input_tokens)))
    rows.append(("mean_output_tokens", str(report.mean_output_tokens)))
    rows.append(("guardrail_drops_by_check", format_drops(report.guardrail_drops_by_check)))
    rows.append(("scenarios", str(report.scenarios)))
    return rows


def format_drops(drops: Mapping[str, int]) -> str:
    if not drops:
        return "none"
    return ", ".join(f"{name}={count}" for name, count in drops.items())


def print_table(report: MetricsReport, extra: Mapping[str, str] | None = None) -> str:
    table = format_table(report, extra)
    print(table)
    return table


def compare_to_baseline(
    report: MetricsReport, baseline: MetricsReport, tolerance: float
) -> list[str]:
    problems: list[str] = []
    for name in RATE_FIELDS:
        current = getattr(report, name)
        expected = getattr(baseline, name)
        if name in PERFECT_FIELDS and expected >= 1.0 and current < 1.0:
            problems.append(f"{name} is {current:.3f}; baseline is 1.0")
        elif current + tolerance < expected:
            problems.append(
                f"{name} is {current:.3f}, more than {tolerance:.3f} below baseline {expected:.3f}"
            )
    return problems


def report_from_json(payload: bytes) -> MetricsReport:
    return MetricsReport.model_validate_json(payload)


def report_to_json(report: MetricsReport) -> bytes:
    return report.model_dump_json(indent=2).encode("utf-8") + b"\n"
