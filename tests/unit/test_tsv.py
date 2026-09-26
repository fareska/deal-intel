from pathlib import Path

import pytest
from pydantic import ValidationError

from deal_intel.contracts.access import AccessLevel, SourceType
from deal_intel.contracts.reference import (
    Account,
    Contact,
    GongCallSummary,
    Opportunity,
    PricingNote,
    UserProfile,
)
from deal_intel.retrieval.dataset import DatasetFile
from deal_intel.retrieval.reference import ReferenceData
from deal_intel.retrieval.tsv import TSV_DELIMITER, TsvRowError, parse_rows

USER_ROW = {
    "user_id": "USR-9001",
    "user_name": "Test User",
    "role": "Account Owner",
    "allowed_account_ids": "ACC-2001, ACC-2002",
    "allowed_source_types": "salesforce, gong",
    "can_view_sensitive_pricing": "false",
    "can_request_approval": "true",
    "can_view_restricted_account": "false",
}


def first_account_cells(synthetic_data: Path) -> tuple[list[str], list[str]]:
    header, first_row = (synthetic_data / DatasetFile.ACCOUNTS).read_text().splitlines()[:2]
    return header.split(TSV_DELIMITER), first_row.split(TSV_DELIMITER)


def write_tsv(path: Path, rows: list[list[str]]) -> Path:
    path.write_text("".join(TSV_DELIMITER.join(cells) + "\n" for cells in rows))
    return path


@pytest.mark.parametrize(
    ("dataset_file", "contract", "expected_rows"),
    [
        (DatasetFile.ACCOUNTS, Account, 3),
        (DatasetFile.OPPORTUNITIES, Opportunity, 3),
        (DatasetFile.CONTACTS, Contact, 15),
        (DatasetFile.PRICING_NOTES, PricingNote, 5),
        (DatasetFile.ACCESS_PERMISSIONS, UserProfile, 6),
        (DatasetFile.GONG_SUMMARIES, GongCallSummary, 27),
    ],
)
def test_parses_every_provided_file(
    synthetic_data: Path, dataset_file: DatasetFile, contract: type, expected_rows: int
) -> None:
    assert len(parse_rows(synthetic_data / dataset_file, contract)) == expected_rows


def test_restricted_account_keeps_its_level(reference: ReferenceData) -> None:
    assert reference.accounts["ACC-2003"].access_level == AccessLevel.RESTRICTED


def test_missing_column_names_file_and_line(synthetic_data: Path, tmp_path: Path) -> None:
    header, row = first_account_cells(synthetic_data)
    path = write_tsv(tmp_path / "accounts_missing_column.tsv", [header[:-1], row[:-1]])

    with pytest.raises(TsvRowError) as raised:
        parse_rows(path, Account)

    assert str(path) in str(raised.value)
    assert ":2:" in str(raised.value)
    assert raised.value.line_number == 2


def test_unknown_access_level_is_rejected(synthetic_data: Path, tmp_path: Path) -> None:
    header, row = first_account_cells(synthetic_data)
    path = write_tsv(tmp_path / "accounts_bad_access_level.tsv", [header, [*row[:-1], "secret"]])

    with pytest.raises(TsvRowError, match="access_level"):
        parse_rows(path, Account)


def test_account_cannot_be_sensitive_pricing(reference: ReferenceData) -> None:
    values = reference.accounts["ACC-2001"].model_dump()
    values["access_level"] = AccessLevel.SENSITIVE_PRICING

    with pytest.raises(ValidationError, match="access_level"):
        Account.model_validate(values)


def test_comma_lists_and_booleans_are_parsed() -> None:
    user = UserProfile.model_validate(USER_ROW)

    assert user.allowed_account_ids == ["ACC-2001", "ACC-2002"]
    assert user.allowed_source_types == [SourceType.SALESFORCE, SourceType.GONG]
    assert user.can_request_approval is True
    assert user.can_view_sensitive_pricing is False


def test_unknown_source_type_is_rejected() -> None:
    with pytest.raises(ValidationError, match="allowed_source_types"):
        UserProfile.model_validate({**USER_ROW, "allowed_source_types": "salesforce,email"})


def test_money_and_percentages_stay_exact(reference: ReferenceData) -> None:
    note = reference.pricing_notes["PN-4004"]

    assert str(note.requested_discount) == "18"
    assert str(note.renewal_uplift) == "-8"
    assert str(reference.opportunities["OPP-1003"].acv) == "1879000"
