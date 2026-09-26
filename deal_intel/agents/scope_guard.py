"""The scope assertion that runs before any evidence reaches a model.

The retriever already filters in SQL; this re-checks the finished pack with the Python mirror of
those predicates, so a bug in either one stops the run instead of leaking evidence.
"""

from collections.abc import Iterable

from deal_intel.contracts.access import AccessScope, SourceType
from deal_intel.contracts.evidence import EvidencePack, PackChunk
from deal_intel.permissions.scope import chunk_is_in_scope


class ScopeViolation(RuntimeError):
    """The message gives counts only: an out-of-scope id can itself reveal that hidden evidence
    exists. `chunk_ids` is for the internal audit trail."""

    def __init__(self, message: str, chunk_ids: Iterable[str] = ()) -> None:
        super().__init__(message)
        self.chunk_ids = tuple(chunk_ids)


def out_of_scope_ids(
    scope: AccessScope,
    chunks: Iterable[PackChunk],
    source_types: Iterable[SourceType] | None = None,
) -> list[str]:
    requested = None if source_types is None else frozenset(source_types)
    return [chunk.chunk_id for chunk in chunks if not chunk_is_in_scope(scope, chunk, requested)]


def assert_pack_in_scope(
    pack: EvidencePack,
    scope: AccessScope,
    source_types: Iterable[SourceType] | None = None,
) -> None:
    """`source_types`, when given, also holds the pack to the agent's own source list."""
    if pack.opportunity_id != scope.opportunity_id:
        raise ScopeViolation("evidence pack was built for a different opportunity")
    offending = out_of_scope_ids(scope, pack.chunks, source_types)
    if offending:
        raise ScopeViolation(f"{len(offending)} pack chunks are outside the scope", offending)


def is_empty_pack(pack: EvidencePack) -> bool:
    return not pack.chunks
