"""Golden brief structure and Slack keyword placement."""

import os
from pathlib import Path

import pytest

from deal_intel.contracts.agents.common import LLM_AGENTS
from deal_intel.contracts.brief import Brief
from deal_intel.evaluation.goldens import (
    comparable_brief,
    golden_path,
    load_golden,
    section_text_by_heading,
    slack_labels,
    write_golden,
)
from deal_intel.evaluation.scenarios import (
    FIXTURES_NOT_RECORDED,
    RECORDED_PAIRS,
    SLACK_GOLDEN_PATH,
    DemoPair,
)
from deal_intel.llm.fixtures import fixtures_recorded
from deal_intel.rendering.brief import latest_brief
from deal_intel.retrieval.slack_dataset import SlackGoldenLabel

REPO_ROOT = Path(__file__).resolve().parents[2]
COMMITTED_FIXTURES = REPO_ROOT / "tests" / "fixtures" / "llm"
WRITE_EVAL_ARTIFACTS = "WRITE_EVAL_ARTIFACTS"


def brief_for(run_bench, run_id: str) -> tuple[Brief, str] | None:
    with run_bench.session_factory() as session:
        row = latest_brief(session, run_id)
    if row is None:
        return None
    return Brief.model_validate(row.json), row.markdown


@pytest.mark.parametrize("pair", RECORDED_PAIRS, ids=lambda pair: pair.label)
def test_golden_section_citations_labels_and_rules(
    run_bench, eval_runs: list[tuple[DemoPair, str]], pair: DemoPair
) -> None:
    run_id = next(run_id for item, run_id in eval_runs if item == pair)
    loaded = brief_for(run_bench, run_id)
    assert loaded is not None
    brief, _markdown = loaded
    current = comparable_brief(brief)
    path = golden_path(pair)
    if os.environ.get(WRITE_EVAL_ARTIFACTS) == "1":
        write_golden(pair, brief)
    if not path.is_file():
        pytest.fail(f"missing golden {path}; set {WRITE_EVAL_ARTIFACTS}=1 to write it")
    assert current == load_golden(pair)


@pytest.mark.skipif(
    not fixtures_recorded(COMMITTED_FIXTURES, LLM_AGENTS), reason=FIXTURES_NOT_RECORDED
)
@pytest.mark.parametrize("pair", RECORDED_PAIRS, ids=lambda pair: pair.label)
def test_slack_keywords_appear_in_expected_sections(
    run_bench, eval_runs: list[tuple[DemoPair, str]], pair: DemoPair
) -> None:
    run_id = next(run_id for item, run_id in eval_runs if item == pair)
    loaded = brief_for(run_bench, run_id)
    assert loaded is not None
    brief, markdown = loaded
    texts = section_text_by_heading(brief, markdown)
    for label in labels_for_opportunity(pair.opportunity_id):
        section = texts[label.expected_section]
        for keyword in label.keywords:
            assert keyword.casefold() in section.casefold(), (label.update_id, keyword)


def test_removing_a_citation_fails_the_golden_compare(run_bench, eval_runs) -> None:
    pair, run_id = next(item for item in eval_runs if item[0] in RECORDED_PAIRS)
    loaded = brief_for(run_bench, run_id)
    assert loaded is not None
    brief, _ = loaded
    current = comparable_brief(brief)
    broken = current.model_copy(update={"sections": {name: [] for name in current.sections}})
    assert broken != current


def labels_for_opportunity(opportunity_id: str) -> list[SlackGoldenLabel]:
    prefix = opportunity_id.removeprefix("OPP-")
    return [
        label
        for label in slack_labels(SLACK_GOLDEN_PATH)
        if label.update_id.startswith(f"SLK-{prefix}")
    ]
