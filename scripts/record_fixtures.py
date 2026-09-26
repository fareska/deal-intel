"""Records live fixtures for the three agents on the authorised demo pairs.

This makes real Anthropic API calls, and the LLM client writes `llm_calls` rows and output-cache
entries to the database in DATABASE_URL. Run it only when you mean to spend tokens:

    RECORD_FIXTURES=1 LLM_CLIENT=anthropic uv run python scripts/record_fixtures.py

Fixtures land in `tests/fixtures/llm/<agent>/<input_hash>.json`. Every call is `fresh`, so an
earlier cached output never stands in for a recording. Superseded fixture files are left in
place; delete them by hand once the new ones are committed.
"""

from decimal import Decimal
from pathlib import Path
from typing import Annotated

import typer
from sqlalchemy.orm import Session

from deal_intel.agents.base import AgentRuntime
from deal_intel.agents.pipeline import AgentOutputs, run_all_agents
from deal_intel.config import LlmClientKind, Settings, get_settings
from deal_intel.contracts.access import Allowed
from deal_intel.contracts.agents.agent_run import AgentRun
from deal_intel.db.session import get_session_factory
from deal_intel.evaluation.scenarios import RECORDED_PAIRS, DemoPair
from deal_intel.llm.client import LlmClient
from deal_intel.llm.factory import build_llm_client
from deal_intel.llm.fixtures import fixture_path
from deal_intel.observability.tracing import NoopTracer
from deal_intel.permissions.gate import authorize
from deal_intel.retrieval.retriever import ScopedRetriever

NOT_RECORDING_MESSAGE = (
    "Refusing to run: set RECORD_FIXTURES=1 and LLM_CLIENT=anthropic. "
    "This script makes live, billed model calls."
)
PAIR_HELP = "A pair to record, e.g. USR-5002/OPP-1002; repeat for several. Default: all three."

PairOption = Annotated[list[str] | None, typer.Option("--pair", help=PAIR_HELP)]

app = typer.Typer(add_completion=False)


@app.command()
def record(pair: PairOption = None) -> None:
    settings = get_settings()
    require_live_recording(settings)
    session_factory = get_session_factory()
    llm = build_llm_client(settings, session_factory, NoopTracer())
    total = Decimal(0)
    for demo in selected_pairs(pair or []):
        with session_factory() as session:
            outputs = record_pair(session, demo, llm, settings)
        total += report_pair(demo, outputs, settings.llm_fixtures_root)
    typer.echo(f"total cost: ${total}")


def require_live_recording(settings: Settings) -> None:
    if not settings.record_fixtures or settings.llm_client is not LlmClientKind.ANTHROPIC:
        typer.echo(NOT_RECORDING_MESSAGE, err=True)
        raise typer.Exit(code=2)


def selected_pairs(labels: list[str]) -> list[DemoPair]:
    if not labels:
        return list(RECORDED_PAIRS)
    by_label = {demo.label: demo for demo in RECORDED_PAIRS}
    unknown = sorted(set(labels) - set(by_label))
    if unknown:
        raise typer.BadParameter(f"unknown pairs {unknown}; choose from {sorted(by_label)}")
    return [by_label[label] for label in labels]


def record_pair(
    session: Session, demo: DemoPair, llm: LlmClient, settings: Settings
) -> AgentOutputs:
    access = authorize(session, demo.user_id, demo.opportunity_id)
    if not isinstance(access, Allowed):
        raise typer.BadParameter(f"{demo.label} is not authorised: {access.reason_code}")
    runtime = AgentRuntime(
        retriever=ScopedRetriever(session, access.scope),
        llm=llm,
        tracer=NoopTracer(),
        settings=settings,
        fresh=True,
    )
    return run_all_agents(session, runtime)


def report_pair(demo: DemoPair, outputs: AgentOutputs, fixtures_root: Path) -> Decimal:
    typer.echo(demo.label)
    runs: list[AgentRun] = [outputs.findings, outputs.stakeholders, outputs.strategy]
    for run in runs:
        typer.echo(f"  {describe_run(run, fixtures_root)}")
    return sum((run.llm_result.cost_usd for run in runs if run.llm_result), Decimal(0))


def describe_run(run: AgentRun, fixtures_root: Path) -> str:
    if run.llm_result is None or run.input_hash is None:
        return f"{run.agent_name}: empty pack, no model call, no fixture"
    result = run.llm_result
    path = fixture_path(fixtures_root, run.agent_name, run.input_hash)
    return (
        f"{run.agent_name}: {path} (attempts={result.attempts}, tool_calls={result.tool_calls}, "
        f"input_tokens={result.usage.input_tokens}, output_tokens={result.usage.output_tokens}, "
        f"cost=${result.cost_usd})"
    )


if __name__ == "__main__":
    app()
