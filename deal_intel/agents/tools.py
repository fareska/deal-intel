"""Read-only evidence tools for the tool loop, bound to one run's `ScopedRetriever`.

Tools only narrow: the agent's source types are intersected with what the model asks for, and
the retriever intersects them again with the scope. Every result passes the scope assertion
before the model sees it, and an id the scope cannot see gets the same answer as an id that does
not exist, so a tool can never confirm that hidden evidence exists.
"""

from collections.abc import Callable, Iterable, Sequence
from enum import StrEnum
from typing import Annotated

from pydantic import Field

from deal_intel.agents.scope_guard import assert_chunks_in_scope
from deal_intel.contracts.access import SourceType
from deal_intel.contracts.base import StrictModel
from deal_intel.contracts.evidence import PackChunk, RetrievalRecord, RetrievalResult
from deal_intel.contracts.guardrails import ChunkId
from deal_intel.contracts.llm import Tool, ToolOutput
from deal_intel.retrieval.retriever import ScopedRetriever

MAX_TOOL_CHUNKS = 8
DEFAULT_SEARCH_K = 5
MAX_QUERY_CHARS = 200
NOT_FOUND_PREFIX = "Not found: "
SEARCH_EVIDENCE_DESCRIPTION = (
    "Full-text search over the evidence this brief may use. Returns up to k chunks, each "
    "framed with its chunk_id. Optionally restrict to some source types."
)
GET_EVIDENCE_DESCRIPTION = (
    "Fetch evidence chunks by chunk_id, for example an id cited by an earlier analysis step. "
    "Ids that do not exist for this brief are reported as not found."
)


class EvidenceToolName(StrEnum):
    SEARCH_EVIDENCE = "search_evidence"
    GET_EVIDENCE = "get_evidence"


class SearchEvidenceArgs(StrictModel):
    query: str = Field(min_length=1, max_length=MAX_QUERY_CHARS)
    source_types: list[SourceType] | None = None
    k: int = Field(default=DEFAULT_SEARCH_K, ge=1, le=MAX_TOOL_CHUNKS)


class GetEvidenceArgs(StrictModel):
    chunk_ids: Annotated[list[ChunkId], Field(min_length=1, max_length=MAX_TOOL_CHUNKS)]


class EvidenceTools:
    """The tools of one agent call. `records` collects a `RetrievalRecord` per tool retrieval,
    so tool reads join the run's audit trail alongside the pack's."""

    def __init__(self, retriever: ScopedRetriever, source_types: frozenset[SourceType]) -> None:
        self._retriever = retriever
        self._source_types = source_types
        self.records: list[RetrievalRecord] = []

    def build(self, names: Iterable[EvidenceToolName]) -> tuple[Tool, ...]:
        factories: dict[EvidenceToolName, Callable[[], Tool]] = {
            EvidenceToolName.SEARCH_EVIDENCE: self._search_tool,
            EvidenceToolName.GET_EVIDENCE: self._get_tool,
        }
        return tuple(factories[name]() for name in names)

    def search_evidence(self, args: SearchEvidenceArgs) -> ToolOutput:
        requested = self._source_types
        if args.source_types is not None:
            requested = requested & frozenset(args.source_types)
        found = self._checked(self._retriever.search(args.query, requested, k=args.k))
        return ToolOutput(chunks=found)

    def get_evidence(self, args: GetEvidenceArgs) -> ToolOutput:
        wanted = list(dict.fromkeys(args.chunk_ids))
        found = self._checked(self._retriever.get(wanted, self._source_types))
        return ToolOutput(chunks=found, note=not_found_note(wanted, found))

    def _checked(self, result: RetrievalResult) -> list[PackChunk]:
        self.records.append(result.record)
        assert_chunks_in_scope(result.chunks, self._retriever.scope, self._source_types)
        return result.chunks

    def _search_tool(self) -> Tool[SearchEvidenceArgs]:
        return Tool[SearchEvidenceArgs](
            name=EvidenceToolName.SEARCH_EVIDENCE,
            description=SEARCH_EVIDENCE_DESCRIPTION,
            arguments=SearchEvidenceArgs,
            run=self.search_evidence,
        )

    def _get_tool(self) -> Tool[GetEvidenceArgs]:
        return Tool[GetEvidenceArgs](
            name=EvidenceToolName.GET_EVIDENCE,
            description=GET_EVIDENCE_DESCRIPTION,
            arguments=GetEvidenceArgs,
            run=self.get_evidence,
        )


def not_found_note(wanted: Sequence[str], found: Sequence[PackChunk]) -> str | None:
    """Names only ids the model itself sent, and gives no reason, so unknown and out-of-scope
    ids read the same."""
    returned = {chunk.chunk_id for chunk in found}
    missing = [chunk_id for chunk_id in wanted if chunk_id not in returned]
    return f"{NOT_FOUND_PREFIX}{', '.join(missing)}" if missing else None
