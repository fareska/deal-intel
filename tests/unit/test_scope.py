import pytest

from deal_intel.contracts.access import AccessLevel, AccessScope, Allowed, SourceType
from deal_intel.contracts.evidence import EvidenceChunk
from deal_intel.permissions.gate import decide
from deal_intel.permissions.scope import (
    chunk_is_in_scope,
    evidence_is_in_scope,
    permitted_levels,
    permitted_source_types,
)
from deal_intel.retrieval.reference import ReferenceData

OTHER_ACCOUNT_MARKERS = (
    *(f"CALL-0{number}" for number in range(10, 28)),
    *(f"CON-{number}" for number in range(3006, 3016)),
    "ACC-2002",
    "ACC-2003",
    "OPP-1002",
    "OPP-1003",
    "PN-4003",
    "PN-4004",
    "PN-4005",
    "SLK-1002",
    "SLK-1003",
)


def scope_for(reference: ReferenceData, user_id: str, opportunity_id: str) -> AccessScope:
    opportunity = reference.opportunities[opportunity_id]
    result = decide(
        reference.users[user_id], opportunity, reference.accounts[opportunity.account_id]
    )
    assert isinstance(result, Allowed)
    return result.scope


def in_scope(scope: AccessScope, chunks: list[EvidenceChunk]) -> list[EvidenceChunk]:
    return [chunk for chunk in chunks if chunk_is_in_scope(scope, chunk)]


@pytest.mark.parametrize(
    ("max_level", "expected"),
    [
        (AccessLevel.STANDARD, (AccessLevel.STANDARD,)),
        (AccessLevel.RESTRICTED, (AccessLevel.STANDARD, AccessLevel.RESTRICTED)),
        (AccessLevel.SENSITIVE_PRICING, tuple(AccessLevel)),
    ],
)
def test_permitted_levels(max_level: AccessLevel, expected: tuple[AccessLevel, ...]) -> None:
    assert permitted_levels(max_level) == expected


def test_requested_source_types_only_narrow_the_scope(reference: ReferenceData) -> None:
    scope = scope_for(reference, "USR-5007", "OPP-1001")

    assert permitted_source_types(scope, [SourceType.PRICING]) == frozenset()
    assert permitted_source_types(scope, [SourceType.GONG, SourceType.SLACK]) == {SourceType.GONG}
    assert permitted_source_types(scope) == scope.source_types


def test_narrow_scope_excludes_slack_pricing_and_policies(
    reference: ReferenceData, evidence_chunks: list[EvidenceChunk]
) -> None:
    visible = in_scope(scope_for(reference, "USR-5007", "OPP-1001"), evidence_chunks)

    assert visible
    assert {chunk.source_type for chunk in visible} == {SourceType.SALESFORCE, SourceType.GONG}


def test_account_owner_sees_own_pricing_and_nothing_from_other_accounts(
    reference: ReferenceData, evidence_chunks: list[EvidenceChunk]
) -> None:
    visible_ids = {
        chunk.chunk_id
        for chunk in in_scope(scope_for(reference, "USR-5001", "OPP-1001"), evidence_chunks)
    }

    assert {"pricing:PN-4001", "pricing:PN-4002", "slack:SLK-1001-01"} <= visible_ids
    assert not [
        chunk_id
        for chunk_id in visible_ids
        for marker in OTHER_ACCOUNT_MARKERS
        if marker in chunk_id
    ]


def test_policy_rules_need_the_policies_source_type(
    reference: ReferenceData, evidence_chunks: list[EvidenceChunk]
) -> None:
    owner = in_scope(scope_for(reference, "USR-5001", "OPP-1001"), evidence_chunks)
    narrow = in_scope(scope_for(reference, "USR-5007", "OPP-1001"), evidence_chunks)

    assert sum(chunk.source_type == SourceType.POLICIES for chunk in owner) == 10
    assert not [chunk for chunk in narrow if chunk.source_type == SourceType.POLICIES]


def test_deal_desk_on_one_opportunity_never_sees_another(
    reference: ReferenceData, evidence_chunks: list[EvidenceChunk]
) -> None:
    visible = in_scope(scope_for(reference, "USR-5005", "OPP-1003"), evidence_chunks)

    assert {chunk.opportunity_id for chunk in visible} == {"OPP-1003", None}
    assert {chunk.account_id for chunk in visible} == {"ACC-2003", None}


def test_restricted_mix_is_filtered_per_row(
    reference: ReferenceData, evidence_chunks: list[EvidenceChunk]
) -> None:
    scope = scope_for(reference, "USR-5003", "OPP-1003").model_copy(
        update={"max_access_level": AccessLevel.RESTRICTED, "sensitive_pricing_allowed": False}
    )
    visible_ids = {chunk.chunk_id for chunk in in_scope(scope, evidence_chunks)}

    assert "slack:SLK-1003-03" in visible_ids
    assert "slack:SLK-1003-01" not in visible_ids
    assert "slack:SLK-1003-02" not in visible_ids
    assert "pricing:PN-4004" not in visible_ids


def test_sensitive_pricing_flag_hides_pricing_even_at_the_top_level(
    reference: ReferenceData, evidence_chunks: list[EvidenceChunk]
) -> None:
    scope = scope_for(reference, "USR-5003", "OPP-1003").model_copy(
        update={"sensitive_pricing_allowed": False}
    )
    visible_ids = {chunk.chunk_id for chunk in in_scope(scope, evidence_chunks)}

    assert "pricing:PN-4004" not in visible_ids
    assert "slack:SLK-1003-02" in visible_ids


def test_snapshot_is_part_of_the_row_predicate(
    reference: ReferenceData, evidence_chunks: list[EvidenceChunk]
) -> None:
    scope = scope_for(reference, "USR-5001", "OPP-1001")
    chunk = next(chunk for chunk in evidence_chunks if chunk_is_in_scope(scope, chunk))

    assert evidence_is_in_scope(scope, chunk.snapshot_id, chunk)
    assert not evidence_is_in_scope(scope, "some-other-snapshot", chunk)
