import pytest
from pydantic import ValidationError
from sqlalchemy.orm import Session

from deal_intel.agents.conversation_intelligence import SPEC as CI_SPEC
from deal_intel.agents.scope_guard import ScopeViolation
from deal_intel.agents.tools import (
    MAX_TOOL_CHUNKS,
    EvidenceTools,
    GetEvidenceArgs,
    SearchEvidenceArgs,
)
from deal_intel.contracts.access import Allowed, SourceType
from deal_intel.contracts.evidence import RetrievalOperation
from deal_intel.guardrails.framing import frame_tool_output
from deal_intel.permissions.gate import authorize
from deal_intel.retrieval.retriever import ScopedRetriever

UNKNOWN_ID = "slack:SLK-9999-99"
PLACEHOLDER = "<id>"
OTHER_ACCOUNT_PRICING = "pricing:PN-4004"
OWN_PRICING = "pricing:PN-4001"
OWN_SLACK = "slack:SLK-1001-01"
OWN_CALL = "gong_summary:CALL-001"


def retriever_for(session: Session, user_id: str, opportunity_id: str) -> ScopedRetriever:
    access = authorize(session, user_id, opportunity_id)
    assert isinstance(access, Allowed)
    return ScopedRetriever(session, access.scope)


def ci_tools(session: Session, user_id: str, opportunity_id: str) -> EvidenceTools:
    return EvidenceTools(retriever_for(session, user_id, opportunity_id), CI_SPEC.source_types)


def get_response(tools: EvidenceTools, chunk_id: str) -> str:
    """What the model reads, with the requested id masked so two answers can be compared."""
    output = tools.get_evidence(GetEvidenceArgs(chunk_ids=[chunk_id]))
    assert output.chunks == []
    return frame_tool_output(output).replace(chunk_id, PLACEHOLDER)


@pytest.mark.parametrize(
    ("user_id", "hidden_id"),
    [
        ("USR-5001", OTHER_ACCOUNT_PRICING),  # another account
        ("USR-5001", OWN_PRICING),  # the user may see it, this agent's sources may not
        ("USR-5007", OWN_SLACK),  # same account, a source outside the user's scope
    ],
)
def test_hidden_and_unknown_ids_get_the_same_not_found_answer(
    ingested_session: Session, user_id: str, hidden_id: str
) -> None:
    tools = ci_tools(ingested_session, user_id, "OPP-1001")

    assert get_response(tools, hidden_id) == get_response(tools, UNKNOWN_ID)


def test_get_evidence_returns_visible_chunks_and_names_the_rest(ingested_session: Session) -> None:
    tools = ci_tools(ingested_session, "USR-5001", "OPP-1001")

    output = tools.get_evidence(GetEvidenceArgs(chunk_ids=[OWN_SLACK, UNKNOWN_ID, OWN_SLACK]))

    assert [chunk.chunk_id for chunk in output.chunks] == [OWN_SLACK]
    assert output.note is not None and UNKNOWN_ID in output.note
    assert [record.operation for record in tools.records] == [RetrievalOperation.GET]


def test_search_is_held_to_the_agents_source_types(ingested_session: Session) -> None:
    tools = ci_tools(ingested_session, "USR-5001", "OPP-1001")

    everything = tools.search_evidence(SearchEvidenceArgs(query="renewal", k=MAX_TOOL_CHUNKS))
    pricing = tools.search_evidence(
        SearchEvidenceArgs(query="renewal", source_types=[SourceType.PRICING])
    )

    assert everything.chunks
    assert {chunk.source_type for chunk in everything.chunks} <= CI_SPEC.source_types
    assert len(everything.chunks) <= MAX_TOOL_CHUNKS
    assert pricing.chunks == []


@pytest.mark.parametrize(
    "arguments",
    [
        {"model": SearchEvidenceArgs, "query": "renewal", "k": MAX_TOOL_CHUNKS + 1},
        {"model": SearchEvidenceArgs, "query": ""},
        {"model": GetEvidenceArgs, "chunk_ids": [OWN_CALL] * (MAX_TOOL_CHUNKS + 1)},
        {"model": GetEvidenceArgs, "chunk_ids": []},
    ],
)
def test_tool_arguments_are_bounded(arguments: dict) -> None:
    model = arguments.pop("model")

    with pytest.raises(ValidationError):
        model.model_validate(arguments)


def test_tool_result_outside_the_scope_raises(
    ingested_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    retriever = retriever_for(ingested_session, "USR-5001", "OPP-1001")
    leaked = retriever_for(ingested_session, "USR-5003", "OPP-1003").get([OTHER_ACCOUNT_PRICING])
    monkeypatch.setattr(retriever, "get", lambda chunk_ids, source_types: leaked)
    tools = EvidenceTools(retriever, frozenset(SourceType))

    with pytest.raises(ScopeViolation) as raised:
        tools.get_evidence(GetEvidenceArgs(chunk_ids=[OTHER_ACCOUNT_PRICING]))

    assert raised.value.chunk_ids == (OTHER_ACCOUNT_PRICING,)
