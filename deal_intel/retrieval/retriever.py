# Keeps annotations lazy: once the class defines `list`, an eager `list[...]` annotation later
# in the class body would resolve to that method and fail at import.
from __future__ import annotations

from collections.abc import Iterable
from datetime import date
from enum import StrEnum

from sqlalchemy import ColumnElement, Select, and_, cast, func, not_, or_, select
from sqlalchemy.dialects.postgresql import REGCONFIG
from sqlalchemy.orm import Session

from deal_intel.config import get_settings
from deal_intel.contracts.access import AccessLevel, AccessScope, SourceType
from deal_intel.contracts.evidence import (
    EvidenceHash,
    EvidencePack,
    PackBuild,
    PackChunk,
    RetrievalFilters,
    RetrievalOperation,
    RetrievalRecord,
    RetrievalResult,
)
from deal_intel.db.models import EvidenceChunkRow
from deal_intel.db.models.evidence import TEXT_SEARCH_CONFIG, chunk_from_row
from deal_intel.permissions.scope import (
    ACCOUNTLESS_SOURCE_TYPES,
    permitted_levels,
    permitted_source_types,
)
from deal_intel.retrieval.ingest import latest_snapshot_id
from deal_intel.retrieval.packing import (
    fill_budget,
    hash_evidence,
    order_for_pack,
    rank_key,
    to_pack_chunk,
)
from deal_intel.retrieval.scoring import BASELINE_LEXICAL, ScoringConfig, final_score

type RequestedTypes = frozenset[SourceType] | None


class NoSnapshot(RuntimeError):
    pass


class ScopedRetriever:
    """Evidence access bound to one `AccessScope`.

    Every statement goes through `_scope_predicates`, and no method accepts an opportunity id,
    so there is no way to ask this class for evidence outside the scope. Ranking uses the latest
    in-scope event date rather than the wall clock, so replays rank identically.
    """

    def __init__(
        self,
        session: Session,
        scope: AccessScope,
        *,
        snapshot_id: str | None = None,
        config: ScoringConfig | None = None,
    ) -> None:
        # A Denied also carries user_id and opportunity_id; without this check a missed branch
        # after authorize() would fail deep inside SQL instead of at the boundary.
        if not isinstance(scope, AccessScope):
            raise TypeError(f"ScopedRetriever needs an AccessScope, got {type(scope).__name__}")
        self._session = session
        self._scope = scope
        self._config = config or ScoringConfig.from_settings(get_settings())
        self.snapshot_id = snapshot_id or require_snapshot(session)
        self.reference_date = self._latest_event_date()

    @property
    def scope(self) -> AccessScope:
        return self._scope

    def list(self, source_types: Iterable[SourceType] | None = None) -> RetrievalResult:
        requested = as_requested(source_types)
        statement = self._scoped_select(requested).order_by(EvidenceChunkRow.chunk_id)
        chunks = [self._scored(row, BASELINE_LEXICAL) for row in self._session.scalars(statement)]
        return self._result(RetrievalOperation.LIST, requested, None, chunks)

    def search(
        self,
        query: str,
        source_types: Iterable[SourceType] | None = None,
        k: int | None = None,
    ) -> RetrievalResult:
        requested = as_requested(source_types)
        # websearch_to_tsquery accepts raw user text and never raises on odd input.
        ts_query = func.websearch_to_tsquery(cast(TEXT_SEARCH_CONFIG, REGCONFIG), query)
        statement = (
            self._scoped_select(requested)
            .add_columns(func.ts_rank_cd(EvidenceChunkRow.tsv, ts_query))
            .where(EvidenceChunkRow.tsv.bool_op("@@")(ts_query))
        )
        scored = [
            self._scored(row, float(lexical)) for row, lexical in self._session.execute(statement)
        ]
        limit = self._config.search_k if k is None else k
        ranked = sorted(scored, key=rank_key)[:limit]
        return self._result(RetrievalOperation.SEARCH, requested, query, ranked)

    def get(
        self, chunk_ids: Iterable[str], source_types: Iterable[SourceType] | None = None
    ) -> RetrievalResult:
        """An id outside the scope is absent from the result exactly as an unknown id is."""
        requested = as_requested(source_types)
        statement = (
            self._scoped_select(requested)
            .where(EvidenceChunkRow.chunk_id.in_(sorted(set(chunk_ids))))
            .order_by(EvidenceChunkRow.chunk_id)
        )
        chunks = [self._scored(row, BASELINE_LEXICAL) for row in self._session.scalars(statement)]
        return self._result(RetrievalOperation.GET, requested, None, chunks)

    def build_pack(
        self,
        agent_name: str,
        budget_tokens: int,
        queries: Iterable[str],
        source_types: Iterable[SourceType] | None = None,
    ) -> PackBuild:
        requested = as_requested(source_types)
        baseline = self.list(requested)
        searches = [self.search(query, requested) for query in queries]
        hits = [hit for search in searches for hit in search.chunks]
        selected, truncated = fill_budget(order_for_pack(baseline.chunks, hits), budget_tokens)
        pack = EvidencePack(
            agent_name=agent_name,
            opportunity_id=self._scope.opportunity_id,
            snapshot_id=self.snapshot_id,
            budget_tokens=budget_tokens,
            estimated_tokens=sum(chunk.estimated_tokens for chunk in selected),
            truncated=truncated,
            chunks=selected,
        )
        return PackBuild(pack=pack, records=[baseline.record, *(s.record for s in searches)])

    def evidence_hash(self) -> EvidenceHash:
        """Fingerprint of everything this scope can see, for run idempotency keys."""
        baseline = self.list()
        return EvidenceHash(value=hash_evidence(baseline.chunks), record=baseline.record)

    def _scoped_select(self, requested: RequestedTypes) -> Select[tuple[EvidenceChunkRow]]:
        return select(EvidenceChunkRow).where(*self._scope_predicates(requested))

    def _scope_predicates(self, requested: RequestedTypes) -> list[ColumnElement[bool]]:
        """The only place a scope becomes SQL; `permissions.scope.chunk_is_in_scope` mirrors it."""
        scope = self._scope
        row = EvidenceChunkRow
        predicates = [
            row.snapshot_id == self.snapshot_id,
            or_(
                row.account_id == scope.account_id,
                row.source_type.in_(plain_values(ACCOUNTLESS_SOURCE_TYPES)),
            ),
            or_(row.opportunity_id == scope.opportunity_id, row.opportunity_id.is_(None)),
            row.source_type.in_(plain_values(permitted_source_types(scope, requested))),
            row.access_level.in_(plain_values(permitted_levels(scope.max_access_level))),
        ]
        if not scope.sensitive_pricing_allowed:
            predicates.append(
                not_(
                    and_(
                        row.source_type == SourceType.PRICING.value,
                        row.access_level == AccessLevel.SENSITIVE_PRICING.value,
                    )
                )
            )
        return predicates

    def _latest_event_date(self) -> date | None:
        statement = select(func.max(EvidenceChunkRow.event_date)).where(
            *self._scope_predicates(None)
        )
        return self._session.scalar(statement)

    def _scored(self, row: EvidenceChunkRow, lexical: float) -> PackChunk:
        chunk = chunk_from_row(row)
        reliability = self._config.reliability_weights[chunk.kind]
        score = final_score(lexical, reliability, chunk.event_date, self.reference_date)
        return to_pack_chunk(chunk, score)

    def _result(
        self,
        operation: RetrievalOperation,
        requested: RequestedTypes,
        query: str | None,
        chunks: list[PackChunk],
    ) -> RetrievalResult:
        record = RetrievalRecord(
            operation=operation,
            snapshot_id=self.snapshot_id,
            filters=self._filters(requested),
            query=query,
            returned_ids=[chunk.chunk_id for chunk in chunks],
            scores={chunk.chunk_id: chunk.score for chunk in chunks},
        )
        return RetrievalResult(chunks=chunks, record=record)

    def _filters(self, requested: RequestedTypes) -> RetrievalFilters:
        scope = self._scope
        return RetrievalFilters(
            account_id=scope.account_id,
            opportunity_id=scope.opportunity_id,
            requested_source_types=None if requested is None else sorted(requested),
            effective_source_types=sorted(permitted_source_types(scope, requested)),
            permitted_levels=list(permitted_levels(scope.max_access_level)),
            sensitive_pricing_allowed=scope.sensitive_pricing_allowed,
        )


def require_snapshot(session: Session) -> str:
    snapshot_id = latest_snapshot_id(session)
    if snapshot_id is None:
        raise NoSnapshot("no evidence snapshot exists; run `deal-intel ingest` first")
    return snapshot_id


def as_requested(source_types: Iterable[SourceType] | None) -> RequestedTypes:
    """Materialised once so a generator argument is not consumed by the first of several uses."""
    return None if source_types is None else frozenset(source_types)


def plain_values(members: Iterable[StrEnum]) -> list[str]:
    return sorted(member.value for member in members)
