"""Write submission artifacts for existing runs through the API services.

Never starts a run. After live `deal-intel generate` (and any approval decisions), export as
the requesting user:

    uv run python scripts/export_artifacts.py --run-id <id> --user USR-5001
    uv run python scripts/export_artifacts.py --run-id <id> --out artifacts/<date>/<scenario>/
    uv run python scripts/export_artifacts.py --run-id <id> --user USR-5001 --dry-run
"""

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Annotated

import typer
from pydantic import BaseModel

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ARTIFACTS_ROOT = REPO_ROOT / "artifacts"
SLACK_UPDATES = REPO_ROOT / "synthetic_data" / "slack" / "account_team_updates.tsv"
SLACK_GOLDEN = REPO_ROOT / "tests" / "fixtures" / "slack_golden.json"
MISSING = "TBD"
README_NAME = "README.md"
RUN_JSON = "run.json"
TRACE_JSON = "trace.json"
LLM_CALLS_JSON = "llm_calls.json"
APPROVALS_BEFORE = "approvals.before.json"
APPROVALS_AFTER = "approvals.after.json"
DATASET_COPIES: tuple[tuple[Path, str, str], ...] = (
    (SLACK_UPDATES, "account_team_updates.tsv", "Synthetic Slack account-team updates"),
    (SLACK_GOLDEN, "slack_golden.json", "Golden labels for Slack updates"),
)
FILE_DESCRIPTIONS: dict[str, str] = {
    RUN_JSON: "Denied run record as the requesting user",
    TRACE_JSON: "Trace spans, redacted as the reader",
    LLM_CALLS_JSON: "LLM call tokens and cost (no prompts)",
    APPROVALS_BEFORE: "Approval requests before decisions",
    APPROVALS_AFTER: "Approval requests and events after decisions",
}

app = typer.Typer(add_completion=False)


@dataclass(frozen=True)
class ExportLayout:
    date_dir: Path
    scenario_dir: Path
    scenario: str
    date: str

    def file(self, name: str) -> Path:
        return self.scenario_dir / name

    def relative(self, name: str) -> str:
        return f"{self.scenario}/{name}"


def scenario_slug(user_id: str | None, opportunity_id: str | None, run_id: str) -> str:
    if user_id and opportunity_id:
        return f"{user_id}_{opportunity_id}"
    return run_id


def export_layout(
    artifacts_root: Path, on_date: str, scenario: str, out: Path | None
) -> ExportLayout:
    if out is not None:
        return ExportLayout(date_dir=out.parent, scenario_dir=out, scenario=out.name, date=on_date)
    date_dir = artifacts_root / on_date
    return ExportLayout(
        date_dir=date_dir, scenario_dir=date_dir / scenario, scenario=scenario, date=on_date
    )


def brief_md_name(version: int) -> str:
    return f"brief.v{version}.md"


def brief_json_name(version: int) -> str:
    return f"brief.v{version}.json"


def brief_filenames(versions: Sequence[int]) -> list[str]:
    return [
        name for version in versions for name in (brief_md_name(version), brief_json_name(version))
    ]


def planned_filenames(*, denied: bool, versions: Sequence[int], has_approvals: bool) -> list[str]:
    if denied:
        return [RUN_JSON, TRACE_JSON]
    names = brief_filenames(versions)
    names.extend([TRACE_JSON, LLM_CALLS_JSON])
    if has_approvals:
        names.extend([APPROVALS_BEFORE, APPROVALS_AFTER])
    return names


def file_description(name: str) -> str:
    if name.startswith("brief.") and name.endswith(".md"):
        return "Brief Markdown"
    if name.startswith("brief.") and name.endswith(".json"):
        return "Brief JSON"
    return FILE_DESCRIPTIONS.get(name, name)


def render_readme(
    on_date: str,
    files: Sequence[tuple[str, str]],
    model_strategy: str,
    model_extraction: str,
    strategy_effort: str,
    total_cost: str,
    commit: str,
) -> str:
    rows = "\n".join(f"| `{path}` | {description} |" for path, description in files)
    return (
        f"# Artifacts {on_date}\n\n"
        f"Commit: `{commit}`\n\n"
        f"## Model settings\n\n"
        f"| Variable | Value |\n"
        f"|---|---|\n"
        f"| `MODEL_STRATEGY` | {model_strategy} |\n"
        f"| `MODEL_EXTRACTION` | {model_extraction} |\n"
        f"| `STRATEGY_EFFORT` | {strategy_effort} |\n\n"
        f"Total cost: {total_cost}\n\n"
        f"## Files\n\n"
        f"| File | Description |\n"
        f"|---|---|\n"
        f"{rows}\n"
    )


def cost_label(value: Decimal | None) -> str:
    return MISSING if value is None else f"${value}"


def commit_hash(repo: Path = REPO_ROOT) -> str | None:
    git = shutil.which("git")
    if git is None:
        return None
    try:
        result = subprocess.run(  # noqa: S603
            [git, "rev-parse", "HEAD"],
            cwd=repo,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return result.stdout.strip() or None


def parse_versions(raw: str) -> list[int]:
    versions = [int(part) for part in raw.split(",") if part.strip()]
    if not versions:
        raise typer.BadParameter("at least one version is required")
    return versions


@app.command()
def export(
    run_id: Annotated[
        list[str], typer.Option("--run-id", help="Existing run id; never creates a run")
    ],
    user: Annotated[
        str | None, typer.Option("--user", help="Reader; defaults to the run's requesting user")
    ] = None,
    opp: Annotated[
        str | None, typer.Option("--opp", help="Opportunity id for dry-run path names")
    ] = None,
    out: Annotated[Path | None, typer.Option("--out", help="Scenario output directory")] = None,
    on_date: Annotated[
        str | None, typer.Option("--date", help="artifacts/<date>/ folder name")
    ] = None,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Print the plan; write nothing")
    ] = False,
    denied: Annotated[
        bool, typer.Option("--denied", help="Dry-run: plan the denial files only")
    ] = False,
    approvals: Annotated[
        bool, typer.Option("--approvals", help="Dry-run: include approval files")
    ] = False,
    versions: Annotated[
        str, typer.Option("--versions", help="Dry-run brief versions, e.g. 1,2")
    ] = "1",
    artifacts_root: Annotated[Path | None, typer.Option("--artifacts-root")] = None,
) -> None:
    """Export one or more existing runs into artifacts/<date>/."""
    if out is not None and len(run_id) > 1:
        typer.echo("--out can be used with a single --run-id", err=True)
        raise typer.Exit(code=2)
    root = artifacts_root or DEFAULT_ARTIFACTS_ROOT
    folder_date = on_date or date.today().isoformat()
    if dry_run:
        print_dry_run(
            run_ids=run_id,
            user=user,
            opp=opp,
            out=out,
            folder_date=folder_date,
            root=root,
            denied=denied,
            has_approvals=approvals,
            versions=parse_versions(versions),
        )
        return
    write_exports(run_id, user, out, folder_date, root)


def print_dry_run(
    run_ids: Sequence[str],
    user: str | None,
    opp: str | None,
    out: Path | None,
    folder_date: str,
    root: Path,
    denied: bool,
    has_approvals: bool,
    versions: Sequence[int],
) -> None:
    from deal_intel.config import DEFAULT_MODEL_EXTRACTION, DEFAULT_MODEL_STRATEGY
    from deal_intel.contracts.llm import Effort

    names = planned_filenames(denied=denied, versions=versions, has_approvals=has_approvals)
    files: list[tuple[str, str]] = []
    for item in run_ids:
        layout = export_layout(root, folder_date, scenario_slug(user, opp, item), out)
        typer.echo(f"{item} -> {layout.scenario_dir}")
        for name in names:
            relative = layout.relative(name)
            files.append((relative, file_description(name)))
            typer.echo(f"  {relative}")
    files.extend(dataset_readme_rows())
    readme = render_readme(
        folder_date,
        files,
        DEFAULT_MODEL_STRATEGY,
        DEFAULT_MODEL_EXTRACTION,
        Effort.HIGH.value,
        MISSING,
        commit_hash() or MISSING,
    )
    typer.echo("")
    typer.echo(f"{folder_date}/{README_NAME}")
    typer.echo(readme)


def write_exports(
    run_ids: Sequence[str],
    user: str | None,
    out: Path | None,
    folder_date: str,
    root: Path,
) -> None:
    from deal_intel.api.errors import NotFound
    from deal_intel.config import get_settings
    from deal_intel.db.session import get_session_factory
    from deal_intel.orchestration.persistence import RunNotFound

    settings = get_settings()
    session_factory = get_session_factory()
    files: list[tuple[str, str]] = []
    costs: list[Decimal] = []
    with session_factory() as session:
        for item in run_ids:
            try:
                reader, opportunity_id, owner_id = resolve_reader(session, item, user)
                layout = export_layout(
                    root, folder_date, scenario_slug(owner_id, opportunity_id, item), out
                )
                written, cost = export_one_run(session, item, reader, layout)
            except (NotFound, RunNotFound):
                typer.echo(f"run {item} not found for this user", err=True)
                raise typer.Exit(code=3) from None
            files.extend(written)
            if cost is not None:
                costs.append(cost)
            typer.echo(f"wrote {layout.scenario_dir}")
    files.extend(copy_dataset(layout_date_dir(root, folder_date, out)))
    readme = render_readme(
        folder_date,
        files,
        settings.model_strategy,
        settings.model_extraction,
        settings.strategy_effort.value,
        cost_label(sum(costs, Decimal(0)) if costs else None),
        commit_hash() or MISSING,
    )
    date_dir = layout_date_dir(root, folder_date, out)
    date_dir.mkdir(parents=True, exist_ok=True)
    (date_dir / README_NAME).write_text(readme, encoding="utf-8")
    typer.echo(f"wrote {date_dir / README_NAME}")


def layout_date_dir(root: Path, folder_date: str, out: Path | None) -> Path:
    return out.parent if out is not None else root / folder_date


def resolve_reader(session, run_id: str, user: str | None) -> tuple[str, str, str]:
    from deal_intel.orchestration.persistence import get_run

    record = get_run(session, run_id)
    reader = user or record.user_id
    return reader, record.opportunity_id, record.user_id


def export_one_run(
    session, run_id: str, reader_user_id: str, layout: ExportLayout
) -> tuple[list[tuple[str, str]], Decimal | None]:
    from deal_intel.api.services.approvals import approvals_for_run
    from deal_intel.api.services.runs import (
        llm_calls_for_reader,
        readable_brief_rows,
        run_status,
        run_trace,
    )
    from deal_intel.contracts.brief import Brief
    from deal_intel.contracts.runs import RunState

    status = run_status(session, run_id, reader_user_id)
    trace = run_trace(session, run_id, reader_user_id)
    layout.scenario_dir.mkdir(parents=True, exist_ok=True)
    if status.state is RunState.DENIED:
        write_model(layout.file(RUN_JSON), status)
        write_model(layout.file(TRACE_JSON), trace)
        return listed_files(layout, (RUN_JSON, TRACE_JSON)), None

    rows = readable_brief_rows(session, run_id, reader_user_id)
    calls = llm_calls_for_reader(session, run_id, reader_user_id)
    records, events = approvals_for_run(session, run_id, reader_user_id)
    written: list[tuple[str, str]] = []
    for row in rows:
        write_brief_version(layout, row, Brief)
        written.extend(listed_files(layout, brief_filenames((row.version,))))
    write_model(layout.file(TRACE_JSON), trace)
    write_models(layout.file(LLM_CALLS_JSON), calls)
    written.extend(listed_files(layout, (TRACE_JSON, LLM_CALLS_JSON)))
    if records:
        write_approval_snapshots(layout, records, events)
        written.extend(listed_files(layout, (APPROVALS_BEFORE, APPROVALS_AFTER)))
    cost = sum((call.cost_usd for call in calls), Decimal(0))
    if cost == 0 and status.cost_usd:
        cost = status.cost_usd
    return written, cost


def listed_files(layout: ExportLayout, names: Sequence[str]) -> list[tuple[str, str]]:
    return [(layout.relative(name), file_description(name)) for name in names]


def write_brief_version(layout: ExportLayout, row, brief_cls) -> None:
    write_text(layout.file(brief_md_name(row.version)), row.markdown)
    write_text(
        layout.file(brief_json_name(row.version)),
        brief_cls.model_validate(row.json).model_dump_json(indent=2),
    )


def write_approval_snapshots(layout: ExportLayout, records, events) -> None:
    write_json(
        layout.file(APPROVALS_BEFORE),
        approval_payload(requests_before_decisions(records, events), []),
    )
    write_json(layout.file(APPROVALS_AFTER), approval_payload(records, events))


def requests_before_decisions(records, events):
    from deal_intel.contracts.approvals import ApprovalStatus

    settled = {
        ApprovalStatus.APPROVED,
        ApprovalStatus.REJECTED,
        ApprovalStatus.EXPIRED,
    }
    decided = {event.approval_id for event in events}
    return [
        record.model_copy(update={"status": ApprovalStatus.PENDING})
        if record.approval_id in decided and record.status in settled
        else record
        for record in records
    ]


def approval_payload(records, events) -> dict:
    return {
        "requests": [item.model_dump(mode="json") for item in records],
        "events": [item.model_dump(mode="json") for item in events],
    }


def copy_dataset(date_dir: Path) -> list[tuple[str, str]]:
    copied: list[tuple[str, str]] = []
    date_dir.mkdir(parents=True, exist_ok=True)
    for source, name, description in DATASET_COPIES:
        if not source.is_file():
            continue
        target = date_dir / name
        target.write_bytes(source.read_bytes())
        copied.append((name, description))
    return copied


def dataset_readme_rows() -> list[tuple[str, str]]:
    return [(name, description) for source, name, description in DATASET_COPIES if source.is_file()]


def write_text(path: Path, text: str) -> None:
    path.write_text(text if text.endswith("\n") else f"{text}\n", encoding="utf-8")


def write_json(path: Path, payload: object) -> None:
    write_text(path, json.dumps(payload, indent=2))


def write_model(path: Path, model: BaseModel) -> None:
    write_text(path, model.model_dump_json(indent=2))


def write_models(path: Path, models: Sequence[BaseModel]) -> None:
    write_json(path, [model.model_dump(mode="json") for model in models])


if __name__ == "__main__":
    app()
