import shutil
from collections import Counter
from datetime import timedelta
from pathlib import Path

import pytest

from deal_intel.contracts.access import AccessLevel
from deal_intel.contracts.reference import GongCallSummary, SlackUpdate
from deal_intel.retrieval.dataset import DatasetFile
from deal_intel.retrieval.reference import ReferenceData
from deal_intel.retrieval.slack_dataset import (
    AUTHORED_UPDATES,
    PHONE_LIKE,
    SLACK_COLUMNS,
    SYNTHETIC_NOTICE,
    SlackDatasetError,
    UpdateKind,
    latest_call_dates,
    load_golden_labels,
    render_tsv,
    validate_updates,
    write_slack_dataset,
)
from deal_intel.retrieval.tsv import TSV_DELIMITER, parse_rows

GOLDEN = Path(__file__).resolve().parents[1] / "fixtures" / "slack_golden.json"
DOCUMENTED_COLUMNS = (
    "update_id",
    "opportunity_id",
    "account_id",
    "update_date",
    "channel",
    "author_role",
    "synthetic_notice",
    "source_access_level",
    "update_text",
)
EXPECTED_LEVELS = {
    "SLK-1001-01": AccessLevel.STANDARD,
    "SLK-1001-02": AccessLevel.STANDARD,
    "SLK-1001-03": AccessLevel.STANDARD,
    "SLK-1002-01": AccessLevel.STANDARD,
    "SLK-1002-02": AccessLevel.STANDARD,
    "SLK-1002-03": AccessLevel.STANDARD,
    "SLK-1003-01": AccessLevel.SENSITIVE_PRICING,
    "SLK-1003-02": AccessLevel.SENSITIVE_PRICING,
    "SLK-1003-03": AccessLevel.RESTRICTED,
}


@pytest.fixture(scope="module")
def summaries(synthetic_data: Path) -> list[GongCallSummary]:
    return parse_rows(synthetic_data / DatasetFile.GONG_SUMMARIES, GongCallSummary)


def authored_update(update_id: str) -> SlackUpdate:
    return next(update for update in AUTHORED_UPDATES if update.update_id == update_id)


def violations_for(
    updates: list[SlackUpdate], reference: ReferenceData, summaries: list[GongCallSummary]
) -> list[str]:
    with pytest.raises(SlackDatasetError) as raised:
        validate_updates(updates, reference, summaries)
    return raised.value.violations


def test_authored_updates_pass_validation(
    reference: ReferenceData, summaries: list[GongCallSummary]
) -> None:
    validate_updates(AUTHORED_UPDATES, reference, summaries)


def test_columns_are_the_documented_ones() -> None:
    assert SLACK_COLUMNS == DOCUMENTED_COLUMNS
    assert render_tsv(AUTHORED_UPDATES).splitlines()[0].split(TSV_DELIMITER) == list(
        DOCUMENTED_COLUMNS
    )


def test_three_updates_per_opportunity() -> None:
    per_opportunity = Counter(update.opportunity_id for update in AUTHORED_UPDATES)

    assert per_opportunity == {"OPP-1001": 3, "OPP-1002": 3, "OPP-1003": 3}


@pytest.mark.parametrize(("update_id", "level"), sorted(EXPECTED_LEVELS.items()))
def test_access_levels_follow_the_plan(update_id: str, level: AccessLevel) -> None:
    assert authored_update(update_id).source_access_level == level


def test_every_update_sits_between_the_last_call_and_the_close(
    reference: ReferenceData, summaries: list[GongCallSummary]
) -> None:
    latest_calls = latest_call_dates(summaries)

    for update in AUTHORED_UPDATES:
        close_date = reference.opportunities[update.opportunity_id].close_date
        assert latest_calls[update.opportunity_id] < update.update_date < close_date


def test_text_has_no_contact_details_or_tsv_breakers() -> None:
    for update in AUTHORED_UPDATES:
        assert "@" not in update.update_text
        assert PHONE_LIKE.search(update.update_text) is None
        assert "\t" not in update.update_text
        assert "\n" not in update.update_text
        assert update.synthetic_notice == SYNTHETIC_NOTICE


def test_update_before_the_last_call_is_rejected(
    reference: ReferenceData, summaries: list[GongCallSummary]
) -> None:
    early = authored_update("SLK-1001-01").model_copy(
        update={"update_date": latest_call_dates(summaries)["OPP-1001"]}
    )

    violations = violations_for([early], reference, summaries)

    assert any("after the latest call" in violation for violation in violations)


def test_update_after_the_close_is_rejected(
    reference: ReferenceData, summaries: list[GongCallSummary]
) -> None:
    close_date = reference.opportunities["OPP-1002"].close_date
    late = authored_update("SLK-1002-01").model_copy(
        update={"update_date": close_date + timedelta(days=1)}
    )

    violations = violations_for([late], reference, summaries)

    assert any("before the close date" in violation for violation in violations)


def test_pricing_keyword_forces_sensitive_pricing(
    reference: ReferenceData, summaries: list[GongCallSummary]
) -> None:
    downgraded = authored_update("SLK-1003-02").model_copy(
        update={"source_access_level": AccessLevel.RESTRICTED}
    )

    violations = violations_for([downgraded], reference, summaries)

    assert violations == ["SLK-1003-02: pricing content must be sensitive_pricing"]


@pytest.mark.parametrize("text", ["A 12% option", "a concession is on the table"])
def test_pricing_keywords_on_standard_accounts_are_rejected(
    reference: ReferenceData, summaries: list[GongCallSummary], text: str
) -> None:
    update = authored_update("SLK-1001-02").model_copy(update={"update_text": text})

    violations = violations_for([update], reference, summaries)

    assert any("sensitive_pricing" in violation for violation in violations)


def test_restricted_account_update_cannot_be_standard(
    reference: ReferenceData, summaries: list[GongCallSummary]
) -> None:
    update = authored_update("SLK-1003-03").model_copy(
        update={"source_access_level": AccessLevel.STANDARD}
    )

    violations = violations_for([update], reference, summaries)

    assert any("below the account's minimum" in violation for violation in violations)


@pytest.mark.parametrize(
    ("field", "value", "problem"),
    [
        ("account_id", "ACC-2002", "account_id does not match"),
        ("update_text", "Mail pavel.stone@example.test for details", "'@'"),
        ("update_text", "Call him on +353 1 555 0199", "phone-like"),
        ("update_text", "one\ttab", "a tab"),
        ("synthetic_notice", "not synthetic", "synthetic_notice"),
        ("update_id", "SLK-1002-09", "opportunity number"),
    ],
)
def test_malformed_updates_are_rejected(
    reference: ReferenceData,
    summaries: list[GongCallSummary],
    field: str,
    value: str,
    problem: str,
) -> None:
    update = authored_update("SLK-1001-03").model_copy(update={field: value})

    violations = violations_for([update], reference, summaries)

    assert any(problem in violation for violation in violations)


def test_duplicate_ids_are_rejected(
    reference: ReferenceData, summaries: list[GongCallSummary]
) -> None:
    update = authored_update("SLK-1001-01")

    violations = violations_for([update, update], reference, summaries)

    assert violations == ["SLK-1001-01: duplicate update_id"]


def test_written_file_round_trips(synthetic_data: Path, tmp_path: Path) -> None:
    root = tmp_path / "synthetic_data"
    shutil.copytree(synthetic_data, root)

    path = write_slack_dataset(root)

    assert path == root / DatasetFile.SLACK_UPDATES
    assert tuple(parse_rows(path, SlackUpdate)) == AUTHORED_UPDATES


def test_golden_labels_cover_exactly_the_authored_updates() -> None:
    labels = load_golden_labels(GOLDEN)

    assert sorted(label.update_id for label in labels) == sorted(
        update.update_id for update in AUTHORED_UPDATES
    )
    assert {label.kind for label in labels} == set(UpdateKind)


def test_verbal_approval_claim_is_labelled_a_conflict() -> None:
    labels = {label.update_id: label for label in load_golden_labels(GOLDEN)}

    assert labels["SLK-1003-02"].kind == UpdateKind.CONFLICTS


def test_committed_file_matches_the_authored_updates(synthetic_data: Path) -> None:
    path = synthetic_data / DatasetFile.SLACK_UPDATES
    if not path.exists():
        pytest.skip("run `deal-intel generate-slack` to create the committed Slack dataset")

    assert path.read_text(encoding="utf-8") == render_tsv(AUTHORED_UPDATES)
