"""The chunks a brief cites, loaded through the run's scope, and the access level they imply."""

from collections.abc import Iterable, Mapping

from deal_intel.contracts.access import AccessLevel
from deal_intel.contracts.evidence import PackChunk
from deal_intel.contracts.runs import AnalysisOutputs
from deal_intel.guardrails.validators import cited_fields
from deal_intel.retrieval.retriever import ScopedRetriever


def cited_chunk_ids(analysis: AnalysisOutputs) -> list[str]:
    agent_ids = (
        chunk_id
        for run in analysis.agent_runs()
        for cited in cited_fields(run.output)
        for item in cited.items
        for chunk_id in item.evidence_ids
    )
    return list(dict.fromkeys([*analysis.snapshot.evidence_ids(), *agent_ids]))


def load_cited_chunks(retriever: ScopedRetriever, chunk_ids: Iterable[str]) -> dict[str, PackChunk]:
    """Through the scope, so a stored output can never put an out-of-scope chunk in a brief."""
    return {chunk.chunk_id: chunk for chunk in retriever.get(chunk_ids).chunks}


def brief_access_level(chunks: Iterable[PackChunk]) -> AccessLevel:
    return max((chunk.access_level for chunk in chunks), default=AccessLevel.STANDARD)


def chunks_by_citation(chunks: Mapping[str, PackChunk]) -> dict[str, PackChunk]:
    return {chunk.citation: chunk for chunk in chunks.values()}
