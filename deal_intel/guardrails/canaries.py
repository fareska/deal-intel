"""Leakage canaries: distinguishing strings and figures from every chunk the reader may not see.

If any canary appears in an output, something leaked. A canary that also occurs in evidence the
reader may see proves nothing, so it is dropped. Canary values are never stored; a hit is
reported by kind and hash only, because the value is itself content the reader may not see.
"""

import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum

from sqlalchemy import select, true
from sqlalchemy.orm import Session

from deal_intel.contracts.access import AccessScope
from deal_intel.contracts.evidence import ChunkKind, EvidenceChunk
from deal_intel.db.models import AccountRow, EvidenceChunkRow, RunEventRow, TraceSpanRow
from deal_intel.db.models.evidence import chunk_from_row
from deal_intel.guardrails.text import Figure, extract_figures, normalise_whitespace
from deal_intel.permissions.scope import chunk_is_in_scope
from deal_intel.retrieval.chunkers.base import LABEL_SEPARATOR
from deal_intel.retrieval.chunkers.gong import SUMMARY_LABEL
from deal_intel.retrieval.hashing import sha256_hex
from deal_intel.retrieval.ingest import latest_snapshot_id

MIN_CANARY_CHARS = 4
PHRASE_WORDS = 6
CONTACT_NAME_KEY = "full_name"
SUMMARY_PREFIX = f"{SUMMARY_LABEL}{LABEL_SEPARATOR}"


class CanaryKind(StrEnum):
    NAME = "name"
    ID = "id"
    PHRASE = "phrase"
    FIGURE = "figure"


@dataclass(frozen=True)
class CanarySet:
    strings: tuple[tuple[CanaryKind, str], ...]
    figures: tuple[Figure, ...]

    def size(self) -> int:
        return len(self.strings) + len(self.figures)

    def without_figures(self) -> "CanarySet":
        """For machine-written text such as span attributes, where token counts and latencies
        are numbers that can equal a hidden figure by chance."""
        return replace(self, figures=())


@dataclass(frozen=True)
class CanaryHit:
    kind: CanaryKind
    canary_sha256: str


def build_canary_set(
    session: Session,
    snapshot_id: str,
    scope: AccessScope | None,
    known_texts: Sequence[str],
) -> CanarySet:
    """`scope` is None for a denied run: then every chunk is hidden. `known_texts` are what the
    reader supplied (their user and opportunity ids), which echoing back reveals nothing."""
    chunks = snapshot_chunks(session, snapshot_id)
    visible = [chunk for chunk in chunks if is_visible(scope, chunk)]
    hidden = [chunk for chunk in chunks if not is_visible(scope, chunk)]
    visible_texts = [*(chunk.text for chunk in visible), *known_texts]
    candidates = [
        *other_account_canaries(session, scope),
        *(item for chunk in hidden for item in chunk_canaries(chunk)),
    ]
    return CanarySet(
        strings=kept_strings(candidates, visible_texts),
        figures=hidden_figures(hidden, visible_texts),
    )


def snapshot_chunks(session: Session, snapshot_id: str) -> list[EvidenceChunk]:
    statement = (
        select(EvidenceChunkRow)
        .where(EvidenceChunkRow.snapshot_id == snapshot_id)
        .order_by(EvidenceChunkRow.chunk_id)
    )
    return [chunk_from_row(row) for row in session.scalars(statement)]


def is_visible(scope: AccessScope | None, chunk: EvidenceChunk) -> bool:
    return scope is not None and chunk_is_in_scope(scope, chunk)


def other_account_canaries(
    session: Session, scope: AccessScope | None
) -> list[tuple[CanaryKind, str]]:
    in_scope = scope.account_id if scope is not None else None
    statement = select(AccountRow.account_id, AccountRow.account_name).where(
        AccountRow.account_id != in_scope if in_scope is not None else true()
    )
    return [
        canary
        for account_id, account_name in session.execute(statement)
        for canary in ((CanaryKind.ID, account_id), (CanaryKind.NAME, account_name))
    ]


def kept_strings(
    candidates: Iterable[tuple[CanaryKind, str]], visible_texts: Sequence[str]
) -> tuple[tuple[CanaryKind, str], ...]:
    visible = folded(" ".join(visible_texts))
    kept = {
        (kind, normalise_whitespace(value))
        for kind, value in candidates
        if len(value) >= MIN_CANARY_CHARS and folded(value) not in visible
    }
    return tuple(sorted(kept))


def chunk_canaries(chunk: EvidenceChunk) -> list[tuple[CanaryKind, str]]:
    canaries = [(CanaryKind.ID, chunk.source_id)]
    if chunk.kind is ChunkKind.CONTACT:
        canaries.append((CanaryKind.NAME, str(chunk.metadata[CONTACT_NAME_KEY])))
    phrase = distinguishing_text(chunk)
    if phrase:
        canaries.append((CanaryKind.PHRASE, first_words(phrase)))
    return canaries


def distinguishing_text(chunk: EvidenceChunk) -> str | None:
    lines = chunk.text.splitlines()
    if chunk.kind is ChunkKind.SLACK:
        # The first line is a generated heading; the update itself follows it.
        return lines[-1]
    if chunk.kind is ChunkKind.POLICY:
        return chunk.text.partition(LABEL_SEPARATOR)[2]
    if chunk.kind is ChunkKind.GONG_SUMMARY:
        summary = next((line for line in lines if line.startswith(SUMMARY_PREFIX)), None)
        return summary.removeprefix(SUMMARY_PREFIX) if summary else None
    return None


def first_words(text: str) -> str:
    return " ".join(text.split()[:PHRASE_WORDS])


def hidden_figures(
    hidden: Iterable[EvidenceChunk], visible_texts: Sequence[str]
) -> tuple[Figure, ...]:
    visible = [figure for text in visible_texts for figure in extract_figures(text)]
    candidates = dict.fromkeys(figure for chunk in hidden for figure in extract_figures(chunk.text))
    return tuple(
        figure for figure in candidates if not any(figure.matches(seen) for seen in visible)
    )


def folded(text: str) -> str:
    return normalise_whitespace(text).casefold()


def find_leaks(texts: Iterable[str], canaries: CanarySet) -> list[CanaryHit]:
    materialised = list(texts)
    corpus = [folded(text) for text in materialised]
    found = [figure for text in materialised for figure in extract_figures(text)]
    hits = [
        canary_hit(kind, value)
        for kind, value in canaries.strings
        if any(folded(value) in text for text in corpus)
    ]
    hits += [
        canary_hit(CanaryKind.FIGURE, figure.label())
        for figure in canaries.figures
        if any(figure.matches(seen) for seen in found)
    ]
    return hits


def canary_hit(kind: CanaryKind, value: str) -> CanaryHit:
    return CanaryHit(kind=kind, canary_sha256=sha256_hex(value.encode("utf-8")))


def surface_leaks(
    session: Session, run_id: str, texts: Sequence[str], canaries: CanarySet
) -> list[CanaryHit]:
    """Output texts plus the run's span attributes, which every reader of traces can see."""
    return [
        *find_leaks(texts, canaries),
        *find_leaks(run_span_texts(session, run_id), canaries.without_figures()),
    ]


def denial_leaks(
    session: Session, run_id: str, known_texts: Sequence[str], payload_texts: Sequence[str]
) -> list[CanaryHit]:
    """For a denied run nothing is in scope, so the canaries come from every chunk."""
    snapshot_id = latest_snapshot_id(session)
    if snapshot_id is None:
        return []
    canaries = build_canary_set(session, snapshot_id, None, known_texts)
    texts = [*payload_texts, *run_event_texts(session, run_id)]
    return surface_leaks(session, run_id, texts, canaries)


def run_event_texts(session: Session, run_id: str) -> list[str]:
    statement = select(RunEventRow.detail).where(RunEventRow.run_id == run_id)
    return [json.dumps(detail, sort_keys=True) for detail in session.scalars(statement)]


def run_span_texts(session: Session, run_id: str) -> list[str]:
    statement = select(TraceSpanRow.name, TraceSpanRow.attributes).where(
        TraceSpanRow.run_id == run_id
    )
    return [
        f"{name} {json.dumps(attributes, sort_keys=True)}"
        for name, attributes in session.execute(statement)
    ]
