import importlib.util
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "export_artifacts.py"
FAKE_RUN_ID = "run-fake"
ON_DATE = "2026-09-26"


@pytest.fixture(scope="module")
def export_mod():
    spec = importlib.util.spec_from_file_location("export_artifacts", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_scenario_paths_use_user_opp_and_date(export_mod) -> None:
    root = Path("artifacts")
    layout = export_mod.export_layout(
        root,
        ON_DATE,
        export_mod.scenario_slug("USR-5001", "OPP-1001", FAKE_RUN_ID),
        None,
    )

    assert layout.scenario_dir == root / ON_DATE / "USR-5001_OPP-1001"
    assert layout.relative("brief.v1.md") == "USR-5001_OPP-1001/brief.v1.md"


def test_out_overrides_scenario_folder(export_mod, tmp_path: Path) -> None:
    out = tmp_path / ON_DATE / "eclipse"
    layout = export_mod.export_layout(tmp_path, ON_DATE, "ignored", out)

    assert layout.scenario_dir == out
    assert layout.date_dir == tmp_path / ON_DATE
    assert layout.relative("trace.json") == "eclipse/trace.json"


def test_denied_plan_is_run_and_trace_only(export_mod) -> None:
    names = export_mod.planned_filenames(denied=True, versions=(1, 2), has_approvals=True)

    assert names == ["run.json", "trace.json"]


def test_approved_plan_lists_versions_trace_and_calls(export_mod) -> None:
    names = export_mod.planned_filenames(denied=False, versions=(1, 2), has_approvals=True)

    assert names == [
        "brief.v1.md",
        "brief.v1.json",
        "brief.v2.md",
        "brief.v2.json",
        "trace.json",
        "llm_calls.json",
        "approvals.before.json",
        "approvals.after.json",
    ]


def test_readme_fills_settings_and_tbd_cost(export_mod) -> None:
    text = export_mod.render_readme(
        ON_DATE,
        [("USR-5001_OPP-1001/brief.v1.md", "Brief Markdown")],
        "claude-opus-5-5",
        "claude-haiku-4-5-20251001",
        "high",
        export_mod.MISSING,
        "abc123",
    )

    assert "`MODEL_STRATEGY` | claude-opus-5-5" in text
    assert "`STRATEGY_EFFORT` | high" in text
    assert "Total cost: TBD" in text
    assert "Commit: `abc123`" in text
    assert "`USR-5001_OPP-1001/brief.v1.md`" in text


def test_dry_run_prints_plan_and_writes_nothing(export_mod, tmp_path: Path) -> None:
    result = CliRunner().invoke(
        export_mod.app,
        [
            "--run-id",
            FAKE_RUN_ID,
            "--user",
            "USR-5001",
            "--opp",
            "OPP-1001",
            "--date",
            ON_DATE,
            "--artifacts-root",
            str(tmp_path),
            "--dry-run",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "USR-5001_OPP-1001/brief.v1.md" in result.output
    assert "trace.json" in result.output
    assert "llm_calls.json" in result.output
    assert "Total cost: TBD" in result.output
    assert "MODEL_STRATEGY" in result.output
    assert not any(path.is_file() for path in tmp_path.rglob("*"))


def test_dry_run_denied_omits_briefs(export_mod, tmp_path: Path) -> None:
    result = CliRunner().invoke(
        export_mod.app,
        [
            "--run-id",
            FAKE_RUN_ID,
            "--user",
            "USR-5007",
            "--opp",
            "OPP-1003",
            "--date",
            ON_DATE,
            "--artifacts-root",
            str(tmp_path),
            "--dry-run",
            "--denied",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "run.json" in result.output
    assert "brief.v1" not in result.output
    assert "llm_calls.json" not in result.output
