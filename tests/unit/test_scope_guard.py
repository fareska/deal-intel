from collections.abc import Sequence

import pytest

from deal_intel.agents.scope_guard import ScopeViolation, assert_pack_in_scope, is_empty_pack
from deal_intel.contracts.access import AccessScope, Allowed, SourceType
from deal_intel.contracts.agents.common import AgentName
from deal_intel.contracts.evidence import EvidenceChunk, EvidencePack
from deal_intel.permissions.gate import decide
from deal_intel.permissions.scope import chunk_is_in_scope
from deal_intel.retrieval.packing import to_pack_chunk
from deal_intel.retrieval.reference import ReferenceData

PACK_SNAPSHOT_ID = "scope-guard-test"
PACK_AGENT = AgentName.CONVERSATION_INTELLIGENCE
PACK_SCORE = 1.0
HIDDEN_PRICING_CHUNK = "pricing:PN-4004"
OWN_SLACK_CHUNK = "slack:SLK-1001-01"
OWN_PRICING_CHUNK = "pricing:PN-4001"


def scope_for(reference: ReferenceData, user_id: str, opportunity_id: str) -> AccessScope:
    opportunity = reference.opportunities[opportunity_id]
    result = decide(
        reference.users[user_id], opportunity, reference.accounts[opportunity.account_id]
    )
    assert isinstance(result, Allowed)
    return result.scope


def pack_of(opportunity_id: str, chunks: Sequence[EvidenceChunk]) -> EvidencePack:
    pack_chunks = [to_pack_chunk(chunk, PACK_SCORE) for chunk in chunks]
    tokens = sum(chunk.estimated_tokens for chunk in pack_chunks)
    return EvidencePack(
        agent_name=PACK_AGENT,
        opportunity_id=opportunity_id,
        snapshot_id=PACK_SNAPSHOT_ID,
        budget_tokens=tokens,
        estimated_tokens=tokens,
        truncated=False,
        chunks=pack_chunks,
    )


def by_id(chunks: Sequence[EvidenceChunk], chunk_id: str) -> EvidenceChunk:
    return next(chunk for chunk in chunks if chunk.chunk_id == chunk_id)


def in_scope_chunks(scope: AccessScope, chunks: Sequence[EvidenceChunk]) -> list[EvidenceChunk]:
    return [chunk for chunk in chunks if chunk_is_in_scope(scope, chunk)]


def test_in_scope_pack_passes(
    reference: ReferenceData, evidence_chunks: list[EvidenceChunk]
) -> None:
    scope = scope_for(reference, "USR-5001", "OPP-1001")

    assert_pack_in_scope(pack_of("OPP-1001", in_scope_chunks(scope, evidence_chunks)), scope)


def test_crafted_out_of_scope_chunk_raises(
    reference: ReferenceData, evidence_chunks: list[EvidenceChunk]
) -> None:
    scope = scope_for(reference, "USR-5001", "OPP-1001")
    crafted = [
        *in_scope_chunks(scope, evidence_chunks),
        by_id(evidence_chunks, HIDDEN_PRICING_CHUNK),
    ]

    with pytest.raises(ScopeViolation) as raised:
        assert_pack_in_scope(pack_of("OPP-1001", crafted), scope)

    assert raised.value.chunk_ids == (HIDDEN_PRICING_CHUNK,)
    assert HIDDEN_PRICING_CHUNK not in str(raised.value)


def test_same_account_chunk_above_the_users_sources_raises(
    reference: ReferenceData, evidence_chunks: list[EvidenceChunk]
) -> None:
    narrow = scope_for(reference, "USR-5007", "OPP-1001")
    crafted = [by_id(evidence_chunks, "sfdc_opp:OPP-1001"), by_id(evidence_chunks, OWN_SLACK_CHUNK)]

    with pytest.raises(ScopeViolation) as raised:
        assert_pack_in_scope(pack_of("OPP-1001", crafted), narrow)

    assert raised.value.chunk_ids == (OWN_SLACK_CHUNK,)


def test_pack_for_another_opportunity_raises(reference: ReferenceData) -> None:
    scope = scope_for(reference, "USR-5005", "OPP-1001")

    with pytest.raises(ScopeViolation, match="different opportunity"):
        assert_pack_in_scope(pack_of("OPP-1002", []), scope)


def test_agent_source_types_narrow_the_check(
    reference: ReferenceData, evidence_chunks: list[EvidenceChunk]
) -> None:
    scope = scope_for(reference, "USR-5001", "OPP-1001")
    pack = pack_of(
        "OPP-1001",
        [by_id(evidence_chunks, OWN_SLACK_CHUNK), by_id(evidence_chunks, OWN_PRICING_CHUNK)],
    )

    assert_pack_in_scope(pack, scope)
    with pytest.raises(ScopeViolation) as raised:
        assert_pack_in_scope(pack, scope, [SourceType.GONG, SourceType.SLACK])
    assert raised.value.chunk_ids == (OWN_PRICING_CHUNK,)


def test_empty_pack_detection(
    reference: ReferenceData, evidence_chunks: list[EvidenceChunk]
) -> None:
    empty = pack_of("OPP-1001", [])

    assert is_empty_pack(empty)
    assert not is_empty_pack(pack_of("OPP-1001", [by_id(evidence_chunks, OWN_SLACK_CHUNK)]))
    assert_pack_in_scope(empty, scope_for(reference, "USR-5001", "OPP-1001"))
