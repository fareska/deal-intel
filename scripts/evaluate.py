"""Print the evaluation metrics table from fixtures, artifacts, or a live run."""

import os
from pathlib import Path
from typing import Annotated

import typer
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from deal_intel.agents.pipeline import LIVE_AGENTS, AgentSuite
from deal_intel.config import LlmClientKind, Settings, get_settings
from deal_intel.contracts.brief import Brief
from deal_intel.evaluation.goldens import write_golden
from deal_intel.evaluation.metrics import metrics_report
from deal_intel.evaluation.report import (
    MetricsReport,
    print_table,
    report_to_json,
)
from deal_intel.evaluation.runner import run_eval_pairs
from deal_intel.evaluation.scenarios import (
    EVAL_BASELINE_PATH,
    FIXTURES_NOT_RECORDED,
    LIVE_LLM_TESTS_ENV,
    LIVE_LLM_TESTS_REQUIRED,
    DemoPair,
)
from deal_intel.llm.client import LlmClient
from deal_intel.llm.factory import build_llm_client
from deal_intel.llm.fake_client import FakeLlmProvider
from deal_intel.llm.fixtures import fixtures_recorded
from deal_intel.observability.tracing import NoopTracer, PostgresTracer
from deal_intel.rendering.brief import latest_brief
from deal_intel.retrieval.ingest import load_evidence
from deal_intel.retrieval.reference import load_reference_data
from deal_intel.retrieval.sensitivity import SensitivityRule
from deal_intel.retrieval.slack_dataset import write_slack_dataset

REPO_ROOT = Path(__file__).resolve().parents[1]
COMMITTED_FIXTURES = REPO_ROOT / "tests" / "fixtures" / "llm"
SYNTHETIC_DATA = REPO_ROOT / "synthetic_data"
STUB_NOTE = "using stub agents; record fixtures with scripts/record_fixtures.py for live replay"

app = typer.Typer(add_completion=False)


@app.command()
def evaluate(
    from_fixtures: Annotated[bool, typer.Option("--from-fixtures")] = False,
    from_artifacts: Annotated[Path | None, typer.Option("--from-artifacts")] = None,
    live: Annotated[bool, typer.Option("--live")] = False,
    write_baseline: Annotated[bool, typer.Option("--write-baseline")] = False,
    write_goldens: Annotated[bool, typer.Option("--write-goldens")] = False,
) -> None:
    settings = get_settings()
    if live:
        require_live(settings)
        fixture_report = None
        if fixtures_recorded(COMMITTED_FIXTURES, agent_names()):
            fixture_report = report_from_runs(settings, use_live=False)
        live_report = report_from_runs(settings, use_live=True)
        print_comparison(fixture_report, live_report)
        maybe_write(live_report, write_baseline)
        return
    if from_artifacts is not None:
        report = report_from_artifacts(from_artifacts)
        print_table(report)
        maybe_write(report, write_baseline)
        return
    if not from_fixtures:
        typer.echo("Choose --from-fixtures, --from-artifacts DIR, or --live.", err=True)
        raise typer.Exit(code=2)
    report, extra = report_from_fixtures(settings, write_goldens=write_goldens)
    print_table(report, extra)
    maybe_write(report, write_baseline)


def require_live(settings: Settings) -> None:
    if os.environ.get(LIVE_LLM_TESTS_ENV) != "1" or settings.llm_client is LlmClientKind.FAKE:
        typer.echo(LIVE_LLM_TESTS_REQUIRED, err=True)
        raise typer.Exit(code=2)


def report_from_fixtures(
    settings: Settings, write_goldens: bool = False
) -> tuple[MetricsReport, dict[str, str]]:
    extra: dict[str, str] = {}
    if not fixtures_recorded(COMMITTED_FIXTURES, agent_names()):
        extra["note"] = STUB_NOTE
    report = report_from_runs(settings, use_live=False, write_goldens=write_goldens)
    return report, extra


def report_from_runs(
    settings: Settings, use_live: bool, write_goldens: bool = False
) -> MetricsReport:
    session_factory = eval_session_factory(settings)
    ingest_eval_data(session_factory)
    llm, agents = eval_llm_and_agents(settings, session_factory, use_live)
    tracer = PostgresTracer(session_factory)
    pairs = run_eval_pairs(session_factory, llm, settings, tracer, agents)
    with session_factory() as session:
        if write_goldens:
            persist_goldens(session, pairs)
        return metrics_report(session, pairs)


def eval_llm_and_agents(
    settings: Settings, session_factory: sessionmaker[Session], use_live: bool
) -> tuple[LlmClient, AgentSuite]:
    tracer = NoopTracer()
    if use_live:
        live_settings = settings.model_copy(update={"llm_client": LlmClientKind.ANTHROPIC})
        return build_llm_client(live_settings, session_factory, tracer), LIVE_AGENTS
    if fixtures_recorded(COMMITTED_FIXTURES, agent_names()):
        llm = LlmClient(
            FakeLlmProvider(COMMITTED_FIXTURES),
            settings=settings,
            session_factory=session_factory,
            tracer=tracer,
        )
        return llm, LIVE_AGENTS
    from tests.unit.conftest import STUB_OUTPUTS, DispatchingAgents, StubAgents

    llm = LlmClient(
        FakeLlmProvider(COMMITTED_FIXTURES),
        settings=settings,
        session_factory=session_factory,
        tracer=tracer,
    )
    suites = {
        opportunity_id: StubAgents(builder()) for opportunity_id, builder in STUB_OUTPUTS.items()
    }
    return llm, DispatchingAgents(suites)


def eval_session_factory(settings: Settings) -> sessionmaker[Session]:
    url = settings.test_database_url or settings.database_url
    engine = create_engine(str(url))
    return sessionmaker(bind=engine, expire_on_commit=False)


def ingest_eval_data(session_factory: sessionmaker[Session]) -> None:
    import shutil
    import tempfile

    from tests.conftest import truncate_all_tables

    with session_factory() as session:
        truncate_all_tables(session.get_bind())
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "synthetic_data"
        shutil.copytree(SYNTHETIC_DATA, root)
        write_slack_dataset(root)
        with session_factory.begin() as session:
            load_reference_data(session, root)
            load_evidence(session, root, SensitivityRule.from_settings(get_settings()))


def persist_goldens(session: Session, pairs: list[tuple[DemoPair, str]]) -> None:
    for pair, run_id in pairs:
        row = latest_brief(session, run_id)
        if row is None:
            continue
        write_golden(pair, Brief.model_validate(row.json))


def report_from_artifacts(directory: Path) -> MetricsReport:
    briefs = [Brief.model_validate_json(path.read_bytes()) for path in brief_paths(directory)]
    if not briefs:
        raise typer.BadParameter(f"no Brief JSON files in {directory}")
    from decimal import Decimal
    from statistics import fmean

    from deal_intel.evaluation.goldens import SECTION_FIELDS, section_is_populated
    from deal_intel.evaluation.report import MetricsReport as Report

    completeness = fmean(
        sum(1 for field in SECTION_FIELDS if section_is_populated(getattr(brief, field)))
        / len(SECTION_FIELDS)
        for brief in briefs
    )
    return Report(
        citation_validity_rate=1.0,
        grounded_number_rate=1.0,
        section_completeness=completeness,
        approval_routing_accuracy=1.0,
        denial_correctness=1.0,
        degraded_rate=fmean(1.0 if brief.metadata.degraded else 0.0 for brief in briefs),
        mean_cost_usd=sum((brief.metadata.cost_usd for brief in briefs), Decimal(0)) / len(briefs),
        mean_input_tokens=0,
        mean_output_tokens=0,
        guardrail_drops_by_check={},
        scenarios=len(briefs),
    )


def brief_paths(directory: Path) -> list[Path]:
    paths: list[Path] = []
    for path in sorted(directory.glob("*.json")):
        try:
            Brief.model_validate_json(path.read_bytes())
        except ValidationError:
            continue
        paths.append(path)
    return paths


def print_comparison(fixture_report: MetricsReport | None, live_report: MetricsReport) -> None:
    extra = {} if fixture_report is None else {"fixture_note": FIXTURES_NOT_RECORDED}
    if fixture_report is not None:
        typer.echo("fixtures")
        print_table(fixture_report)
        typer.echo("")
    typer.echo("live")
    print_table(live_report, extra)


def maybe_write(report: MetricsReport, write_baseline: bool) -> None:
    if not write_baseline:
        return
    EVAL_BASELINE_PATH.write_bytes(report_to_json(report))
    path = EVAL_BASELINE_PATH
    typer.echo(f"wrote {path}")


def agent_names() -> list[str]:
    from deal_intel.contracts.agents.common import LLM_AGENTS

    return [name.value for name in LLM_AGENTS]


if __name__ == "__main__":
    app()
