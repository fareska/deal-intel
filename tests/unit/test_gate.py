from pathlib import Path
from typing import cast

import pytest
from sqlalchemy.orm import Session

from deal_intel.contracts.access import (
    DENIED_MESSAGE,
    AccessLevel,
    Allowed,
    DenialReason,
    Denied,
    SourceType,
)
from deal_intel.permissions.gate import InvalidInput, authorize, decide
from deal_intel.retrieval.reference import ReferenceData, load_reference_data

MATRIX: dict[tuple[str, str], AccessLevel | DenialReason] = {
    ("USR-5001", "OPP-1001"): AccessLevel.STANDARD,
    ("USR-5001", "OPP-1002"): DenialReason.ACCOUNT_NOT_ALLOWED,
    ("USR-5001", "OPP-1003"): DenialReason.ACCOUNT_NOT_ALLOWED,
    ("USR-5002", "OPP-1001"): DenialReason.ACCOUNT_NOT_ALLOWED,
    ("USR-5002", "OPP-1002"): AccessLevel.STANDARD,
    ("USR-5002", "OPP-1003"): DenialReason.ACCOUNT_NOT_ALLOWED,
    ("USR-5003", "OPP-1001"): DenialReason.ACCOUNT_NOT_ALLOWED,
    ("USR-5003", "OPP-1002"): DenialReason.ACCOUNT_NOT_ALLOWED,
    ("USR-5003", "OPP-1003"): AccessLevel.SENSITIVE_PRICING,
    ("USR-5004", "OPP-1001"): AccessLevel.STANDARD,
    ("USR-5004", "OPP-1002"): AccessLevel.STANDARD,
    ("USR-5004", "OPP-1003"): DenialReason.ACCOUNT_NOT_ALLOWED,
    ("USR-5005", "OPP-1001"): AccessLevel.SENSITIVE_PRICING,
    ("USR-5005", "OPP-1002"): AccessLevel.SENSITIVE_PRICING,
    ("USR-5005", "OPP-1003"): AccessLevel.SENSITIVE_PRICING,
    ("USR-5007", "OPP-1001"): AccessLevel.STANDARD,
    ("USR-5007", "OPP-1002"): DenialReason.ACCOUNT_NOT_ALLOWED,
    ("USR-5007", "OPP-1003"): DenialReason.ACCOUNT_NOT_ALLOWED,
}

LEAK_MARKERS = ("Eclipse", "BioMaterials", "ACC-2003")

MALFORMED_IDENTIFIERS = [
    ("bad", "OPP-1001"),
    ("USR-501", "OPP-1001"),
    ("usr-5001", "OPP-1001"),
    (" USR-5001", "OPP-1001"),
    ("USR-5001\n", "OPP-1001"),
    ("OPP-1001", "USR-5001"),
    ("USR-5001", "OPP-10011"),
    ("USR-5001", "opp-1001"),
    ("USR-5001", ""),
    ("USR-5001", "OPP-1001; DROP TABLE users"),
]


class SpySession:
    """Records any use at all, so a test can prove validation ran before every lookup."""

    def __init__(self) -> None:
        self.touched: list[str] = []

    def __getattr__(self, name: str) -> object:
        self.touched.append(name)
        raise AssertionError(f"session.{name} used before input validation")


def decide_for(reference: ReferenceData, user_id: str, opportunity_id: str) -> Allowed | Denied:
    opportunity = reference.opportunities[opportunity_id]
    return decide(reference.users[user_id], opportunity, reference.accounts[opportunity.account_id])


@pytest.mark.parametrize(("user_id", "opportunity_id"), sorted(MATRIX))
def test_gate_matrix(reference: ReferenceData, user_id: str, opportunity_id: str) -> None:
    result = decide_for(reference, user_id, opportunity_id)

    expected = MATRIX[(user_id, opportunity_id)]
    if isinstance(expected, DenialReason):
        assert isinstance(result, Denied)
        assert result.reason_code == expected
    else:
        assert isinstance(result, Allowed)
        assert result.scope.max_access_level == expected


def test_matrix_covers_every_user_and_opportunity(reference: ReferenceData) -> None:
    assert set(MATRIX) == {
        (user_id, opportunity_id)
        for user_id in reference.users
        for opportunity_id in reference.opportunities
    }


def test_unauthorized_requester_gets_narrow_scope(reference: ReferenceData) -> None:
    result = decide_for(reference, "USR-5007", "OPP-1001")

    assert isinstance(result, Allowed)
    assert result.scope.source_types == {SourceType.SALESFORCE, SourceType.GONG}
    assert not result.scope.pricing_allowed
    assert not result.scope.sensitive_pricing_allowed
    assert not result.scope.policies_allowed
    assert not result.scope.can_request_approval


def test_restricted_owner_scope(reference: ReferenceData) -> None:
    result = decide_for(reference, "USR-5003", "OPP-1003")

    assert isinstance(result, Allowed)
    assert result.scope.account_id == "ACC-2003"
    assert result.scope.pricing_allowed
    assert result.scope.sensitive_pricing_allowed
    assert result.scope.policies_allowed


def test_membership_is_checked_before_restriction(reference: ReferenceData) -> None:
    result = decide_for(reference, "USR-5004", "OPP-1003")

    assert isinstance(result, Denied)
    assert result.reason_code == DenialReason.ACCOUNT_NOT_ALLOWED


def test_restricted_account_denies_member_without_restricted_access(
    reference: ReferenceData,
) -> None:
    user = reference.users["USR-5001"].model_copy(update={"allowed_account_ids": ["ACC-2003"]})
    opportunity = reference.opportunities["OPP-1003"]

    result = decide(user, opportunity, reference.accounts["ACC-2003"])

    assert isinstance(result, Denied)
    assert result.reason_code == DenialReason.RESTRICTED_ACCOUNT


def test_restricted_opportunity_on_standard_account_is_restricted(
    reference: ReferenceData,
) -> None:
    opportunity = reference.opportunities["OPP-1001"].model_copy(update={"restricted_access": True})

    result = decide(reference.users["USR-5001"], opportunity, reference.accounts["ACC-2001"])

    assert isinstance(result, Denied)
    assert result.reason_code == DenialReason.RESTRICTED_ACCOUNT


def test_access_levels_are_ordered() -> None:
    assert AccessLevel.STANDARD < AccessLevel.RESTRICTED < AccessLevel.SENSITIVE_PRICING
    assert AccessLevel.SENSITIVE_PRICING > AccessLevel.RESTRICTED >= AccessLevel.RESTRICTED
    assert AccessLevel.STANDARD <= AccessLevel.STANDARD
    assert sorted(AccessLevel, reverse=True) == [
        AccessLevel.SENSITIVE_PRICING,
        AccessLevel.RESTRICTED,
        AccessLevel.STANDARD,
    ]


@pytest.mark.parametrize("plain", ["standard", "zzz"])
def test_access_level_refuses_to_order_against_plain_strings(plain: str) -> None:
    with pytest.raises(TypeError):
        _ = AccessLevel.RESTRICTED < plain
    with pytest.raises(TypeError):
        _ = plain < AccessLevel.RESTRICTED


@pytest.mark.parametrize(("user_id", "opportunity_id"), MALFORMED_IDENTIFIERS)
def test_malformed_input_touches_no_lookup(user_id: str, opportunity_id: str) -> None:
    spy = SpySession()

    with pytest.raises(InvalidInput):
        authorize(cast(Session, spy), user_id, opportunity_id)

    assert spy.touched == []


def test_denial_serialises_without_account_data(reference: ReferenceData) -> None:
    denied = decide_for(reference, "USR-5004", "OPP-1003")

    assert isinstance(denied, Denied)
    payload = denied.model_dump_json()
    assert not [marker for marker in LEAK_MARKERS if marker in payload]
    assert set(Denied.model_fields) == {"reason_code", "user_id", "opportunity_id"}
    assert denied.message == DENIED_MESSAGE


def test_authorize_allows_restricted_owner(db_session: Session, synthetic_data: Path) -> None:
    load_reference_data(db_session, synthetic_data)

    result = authorize(db_session, "USR-5003", "OPP-1003")

    assert isinstance(result, Allowed)
    assert result.scope.max_access_level == AccessLevel.SENSITIVE_PRICING


@pytest.mark.parametrize(
    ("user_id", "opportunity_id", "reason"),
    [
        ("USR-9999", "OPP-1001", DenialReason.UNKNOWN_USER),
        ("USR-5001", "OPP-9999", DenialReason.UNKNOWN_OPPORTUNITY),
        ("USR-5007", "OPP-1003", DenialReason.ACCOUNT_NOT_ALLOWED),
    ],
)
def test_authorize_denials_share_one_message(
    db_session: Session,
    synthetic_data: Path,
    user_id: str,
    opportunity_id: str,
    reason: DenialReason,
) -> None:
    load_reference_data(db_session, synthetic_data)

    result = authorize(db_session, user_id, opportunity_id)

    assert isinstance(result, Denied)
    assert result.reason_code == reason
    assert result.message == DENIED_MESSAGE
