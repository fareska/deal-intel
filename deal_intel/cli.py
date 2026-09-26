from importlib.metadata import version
from pathlib import Path
from typing import Annotated

import typer

from deal_intel.db.session import session_scope
from deal_intel.retrieval.dataset import DEFAULT_DATA_ROOT
from deal_intel.retrieval.ingest import IngestReport, load_evidence
from deal_intel.retrieval.reference import load_reference_data
from deal_intel.retrieval.slack_dataset import AUTHORED_UPDATES, write_slack_dataset

app = typer.Typer(help="Strategic Deal Intelligence Assistant", no_args_is_help=True)

DataRoot = Annotated[Path, typer.Option("--path", help="Folder holding the synthetic dataset")]


@app.callback()
def main() -> None:
    """Keeps Typer in multi-command mode."""


@app.command("version")
def show_version() -> None:
    typer.echo(version("deal-intel"))


@app.command()
def ingest(path: DataRoot = DEFAULT_DATA_ROOT) -> None:
    """Load reference tables and evidence chunks in one transaction."""
    with session_scope() as session:
        row_counts = load_reference_data(session, path)
        report = load_evidence(session, path)
    print_counts(row_counts)
    print_ingest_report(report)


@app.command("generate-slack")
def generate_slack(path: DataRoot = DEFAULT_DATA_ROOT) -> None:
    """Validate the authored Slack updates and write them as a TSV into the dataset."""
    written = write_slack_dataset(path)
    typer.echo(f"wrote {len(AUTHORED_UPDATES)} updates to {written}")


def print_counts(counts: dict[str, int]) -> None:
    for name, count in counts.items():
        typer.echo(f"{name}: {count}")


def print_ingest_report(report: IngestReport) -> None:
    outcome = "unchanged, nothing written" if report.skipped else "written"
    typer.echo(f"snapshot {report.snapshot_id}: {outcome}")
    print_counts({kind.value: count for kind, count in report.chunk_counts.items()})


if __name__ == "__main__":
    app()
