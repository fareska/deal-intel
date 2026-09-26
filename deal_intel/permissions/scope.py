"""The Python mirror of the retriever's SQL scope predicates.

`ScopedRetriever._scope_predicates` and `chunk_is_in_scope` express the same conditions; tests
check every row the SQL returns against this module, so changing one without the other fails.
"""

from collections.abc import Iterable
from typing import Protocol

from deal_intel.contracts.access import AccessLevel, AccessScope, SourceType
from deal_intel.contracts.evidence import EvidenceChunk

# Policy rules belong to no account, so they pass the account condition by source type.
ACCOUNTLESS_SOURCE_TYPES: frozenset[SourceType] = frozenset({SourceType.POLICIES})


class ScopedChunk(Protocol):
    @property
    def source_type(self) -> SourceType: ...

    @property
    def account_id(self) -> str | None: ...

    @property
    def opportunity_id(self) -> str | None: ...

    @property
    def access_level(self) -> AccessLevel: ...


def permitted_levels(max_level: AccessLevel) -> tuple[AccessLevel, ...]:
    return tuple(level for level in AccessLevel if level <= max_level)


def permitted_source_types(
    scope: AccessScope, requested: Iterable[SourceType] | None = None
) -> frozenset[SourceType]:
    """Requested types are intersected with the scope, never added to it."""
    if requested is None:
        return scope.source_types
    return scope.source_types & frozenset(requested)


def is_hidden_sensitive_pricing(
    scope: AccessScope, source_type: SourceType, access_level: AccessLevel
) -> bool:
    return (
        not scope.sensitive_pricing_allowed
        and source_type == SourceType.PRICING
        and access_level == AccessLevel.SENSITIVE_PRICING
    )


def chunk_is_in_scope(
    scope: AccessScope, chunk: ScopedChunk, requested: Iterable[SourceType] | None = None
) -> bool:
    return (
        (chunk.account_id == scope.account_id or chunk.source_type in ACCOUNTLESS_SOURCE_TYPES)
        and chunk.opportunity_id in (scope.opportunity_id, None)
        and chunk.source_type in permitted_source_types(scope, requested)
        and chunk.access_level in permitted_levels(scope.max_access_level)
        and not is_hidden_sensitive_pricing(scope, chunk.source_type, chunk.access_level)
    )


def evidence_is_in_scope(scope: AccessScope, snapshot_id: str, chunk: EvidenceChunk) -> bool:
    return chunk.snapshot_id == snapshot_id and chunk_is_in_scope(scope, chunk)
