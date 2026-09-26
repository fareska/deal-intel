"""Post-generation validators. Each is a pure function `(output, evidence) -> GuardrailReport`:
no model, no database, so it behaves the same at generation, at replay, and in tests.

Validators check every top-level field holding an `EvidenceBacked` item, an optional one, or a
list of them. A report's output is the cleaned version (items dropped or modified), and its
feedback holds one message per problem for the retry policy to send back to the model.
"""

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from functools import cached_property

from pydantic import BaseModel, JsonValue

from deal_intel.contracts.evidence import ChunkKind, PackChunk, make_chunk_id
from deal_intel.contracts.guardrails import (
    OUTPUT_ITEM_REF,
    EvidenceBacked,
    GuardrailCheck,
    GuardrailOutcome,
    GuardrailReport,
    GuardrailResult,
    OutputCheck,
)
from deal_intel.guardrails.text import (
    extract_figures,
    normalise_whitespace,
    unquote_unverified,
)

EVIDENCE_IDS_FIELD = "evidence_ids"
NAME_FIELD = "name"
CONTACT_ID_FIELD = "contact_id"

type Validator[OutputT: BaseModel] = Callable[[OutputT, EvidenceIndex], GuardrailReport[OutputT]]
type TextFieldValue = str | list[str]


@dataclass(frozen=True)
class EvidenceIndex:
    """The chunks an output may cite: the pack plus whatever tools returned during the call."""

    texts: Mapping[str, str]

    @classmethod
    def from_chunks(cls, chunks: Iterable[PackChunk]) -> "EvidenceIndex":
        return cls(texts={chunk.chunk_id: chunk.text for chunk in chunks})

    def has(self, chunk_id: str) -> bool:
        return chunk_id in self.texts

    def cited_texts(self, chunk_ids: Iterable[str]) -> list[str]:
        return [self.texts[chunk_id] for chunk_id in chunk_ids if chunk_id in self.texts]

    def mentions(self, phrase: str) -> bool:
        needle = normalise_whitespace(phrase).casefold()
        return any(needle in text for text in self._folded_texts)

    @cached_property
    def _folded_texts(self) -> list[str]:
        return [normalise_whitespace(text).casefold() for text in self.texts.values()]


@dataclass(frozen=True)
class Problem:
    detail: str
    feedback: str


@dataclass(frozen=True)
class Review:
    """`item` is None when the item is dropped, or the (possibly modified) item to keep."""

    item: EvidenceBacked | None
    problems: tuple[Problem, ...] = field(default=())


type Reviewer = Callable[[EvidenceBacked, EvidenceIndex], Review]


@dataclass(frozen=True)
class CitedField:
    """An output field holding one evidence-backed item or a list of them."""

    name: str
    items: tuple[EvidenceBacked, ...]
    is_list: bool

    def item_refs(self) -> list[str]:
        if not self.is_list:
            return [self.name]
        return [f"{self.name}[{index}]" for index in range(len(self.items))]

    def rebuilt(
        self, kept: Sequence[EvidenceBacked]
    ) -> list[EvidenceBacked] | EvidenceBacked | None:
        """A dropped single item becomes None; a field that cannot be None then fails the
        contract check that follows a final drop."""
        if self.is_list:
            return list(kept)
        return kept[0] if kept else None


def cited_fields(output: BaseModel) -> list[CitedField]:
    fields: list[CitedField] = []
    for name, value in output:
        if isinstance(value, EvidenceBacked):
            fields.append(CitedField(name=name, items=(value,), is_list=False))
        elif (
            isinstance(value, list)
            and value
            and all(isinstance(item, EvidenceBacked) for item in value)
        ):
            fields.append(CitedField(name=name, items=tuple(value), is_list=True))
    return fields


def text_fields(item: BaseModel) -> dict[str, TextFieldValue]:
    """The free-text fields of an item; ids and enum values are not claims."""
    fields: dict[str, TextFieldValue] = {}
    for name, value in item:
        if name == EVIDENCE_IDS_FIELD or isinstance(value, Enum):
            continue
        if isinstance(value, str):
            fields[name] = value
        elif isinstance(value, list) and value and all(isinstance(v, str) for v in value):
            fields[name] = value
    return fields


def item_text(item: BaseModel) -> str:
    parts = [part for value in text_fields(item).values() for part in as_list(value)]
    return " ".join(parts)


def as_list(value: TextFieldValue) -> list[str]:
    return [value] if isinstance(value, str) else value


def review_items[OutputT: BaseModel](
    output: OutputT, check: GuardrailCheck, evidence: EvidenceIndex, reviewer: Reviewer
) -> GuardrailReport[OutputT]:
    results: list[GuardrailResult] = []
    feedback: list[str] = []
    updates: dict[str, object] = {}
    for cited in cited_fields(output):
        kept: list[EvidenceBacked] = []
        for item_ref, item in zip(cited.item_refs(), cited.items, strict=True):
            review = reviewer(item, evidence)
            outcome = GuardrailOutcome.DROPPED if review.item is None else GuardrailOutcome.MODIFIED
            for problem in review.problems:
                results.append(
                    GuardrailResult(
                        check=check, outcome=outcome, item_ref=item_ref, detail=problem.detail
                    )
                )
                feedback.append(f"{item_ref}: {problem.feedback}")
            if review.item is not None:
                kept.append(review.item)
        if review_changed(cited.items, kept):
            updates[cited.name] = cited.rebuilt(kept)
    cleaned = output.model_copy(update=updates) if updates else output
    return GuardrailReport(
        output=cleaned, results=tuple(results) or passed(check), feedback=tuple(feedback)
    )


def review_changed(items: Sequence[EvidenceBacked], kept: Sequence[EvidenceBacked]) -> bool:
    return len(items) != len(kept) or any(a is not b for a, b in zip(items, kept, strict=True))


def passed(check: GuardrailCheck) -> tuple[GuardrailResult, ...]:
    """One record per check that found nothing, so an empty list never reads as "not run"."""
    return (
        GuardrailResult(check=check, outcome=GuardrailOutcome.PASSED, item_ref=OUTPUT_ITEM_REF),
    )


def review_citations(item: EvidenceBacked, evidence: EvidenceIndex) -> Review:
    unknown = [chunk_id for chunk_id in item.evidence_ids if not evidence.has(chunk_id)]
    if not unknown:
        return Review(item)
    listed = ", ".join(unknown)
    problem = Problem(
        detail=listed,
        feedback=f"cites {listed}, which is not in the evidence; cite only chunk ids you were "
        "given.",
    )
    return Review(None, (problem,))


def review_numbers(item: EvidenceBacked, evidence: EvidenceIndex) -> Review:
    cited = [
        figure
        for text in evidence.cited_texts(item.evidence_ids)
        for figure in extract_figures(text)
    ]
    ungrounded = [
        figure.label()
        for figure in extract_figures(item_text(item))
        if not any(figure.matches(source) for source in cited)
    ]
    if not ungrounded:
        return Review(item)
    listed = ", ".join(ungrounded)
    problem = Problem(
        detail=listed,
        feedback=f"states {listed}, which does not appear in the cited evidence; use only "
        "figures from the chunks you cite, or leave them out.",
    )
    return Review(None, (problem,))


def review_quotes(item: EvidenceBacked, evidence: EvidenceIndex) -> Review:
    cited = [normalise_whitespace(text) for text in evidence.cited_texts(item.evidence_ids)]

    def is_verbatim(quote: str) -> bool:
        needle = normalise_whitespace(quote)
        return any(needle in text for text in cited)

    updates: dict[str, TextFieldValue] = {}
    failed: list[str] = []
    for name, value in text_fields(item).items():
        rewritten = [unquote_unverified(text, is_verbatim) for text in as_list(value)]
        failed.extend(quote for _, quotes in rewritten for quote in quotes)
        texts = [text for text, _ in rewritten]
        updates[name] = texts[0] if isinstance(value, str) else texts
    if not failed:
        return Review(item)
    problems = tuple(
        Problem(
            detail=quote,
            feedback=f'quotes "{quote}", which is not verbatim in the cited evidence; quote '
            "exactly or paraphrase without quotation marks.",
        )
        for quote in failed
    )
    return Review(item.model_copy(update=updates), problems)


def review_names(item: EvidenceBacked, evidence: EvidenceIndex) -> Review:
    problems: list[Problem] = []
    name = getattr(item, NAME_FIELD, None)
    if isinstance(name, str) and not evidence.mentions(name):
        problems.append(
            Problem(detail=name, feedback=f"names {name}, who does not appear in the evidence.")
        )
    contact_id = getattr(item, CONTACT_ID_FIELD, None)
    if isinstance(contact_id, str) and not evidence.has(
        make_chunk_id(ChunkKind.CONTACT, contact_id)
    ):
        problems.append(
            Problem(
                detail=contact_id,
                feedback=f"uses contact id {contact_id}, which is not in the evidence.",
            )
        )
    return Review(None, tuple(problems)) if problems else Review(item)


def validate_citations[OutputT: BaseModel](
    output: OutputT, evidence: EvidenceIndex
) -> GuardrailReport[OutputT]:
    return review_items(output, GuardrailCheck.CITATIONS, evidence, review_citations)


def validate_numbers[OutputT: BaseModel](
    output: OutputT, evidence: EvidenceIndex
) -> GuardrailReport[OutputT]:
    return review_items(output, GuardrailCheck.NUMBERS, evidence, review_numbers)


def validate_quotes[OutputT: BaseModel](
    output: OutputT, evidence: EvidenceIndex
) -> GuardrailReport[OutputT]:
    return review_items(output, GuardrailCheck.QUOTES, evidence, review_quotes)


def validate_names[OutputT: BaseModel](
    output: OutputT, evidence: EvidenceIndex
) -> GuardrailReport[OutputT]:
    return review_items(output, GuardrailCheck.NAMES, evidence, review_names)


# Citations run first, so the later checks only read cited chunks that exist.
FINDING_CHECKS: tuple[Validator, ...] = (validate_citations, validate_numbers, validate_quotes)
STAKEHOLDER_CHECKS: tuple[Validator, ...] = (validate_citations, validate_names, validate_quotes)


def run_checks[OutputT: BaseModel](
    output: OutputT, evidence: EvidenceIndex, validators: Sequence[Validator]
) -> GuardrailReport[OutputT]:
    """Applies validators in order, each to the previous one's cleaned output."""
    results: list[GuardrailResult] = []
    feedback: list[str] = []
    for validator in validators:
        report = validator(output, evidence)
        output = report.output
        results.extend(report.results)
        feedback.extend(report.feedback)
    return GuardrailReport(output=output, results=tuple(results), feedback=tuple(feedback))


def evidence_check[OutputT: BaseModel](
    pack_chunks: Sequence[PackChunk], validators: Sequence[Validator]
) -> OutputCheck[OutputT]:
    def check(output: OutputT, tool_chunks: Sequence[PackChunk]) -> GuardrailReport[OutputT]:
        return run_checks(
            output, EvidenceIndex.from_chunks([*pack_chunks, *tool_chunks]), validators
        )

    return check


def enforce_bounds(
    raw: dict[str, JsonValue], output_model: type[BaseModel]
) -> GuardrailReport[dict[str, JsonValue]]:
    """Works on the raw JSON because an over-long list never survives `model_validate`, and the
    provider's structured outputs do not enforce `maxItems`."""
    bounded = dict(raw)
    results: list[GuardrailResult] = []
    feedback: list[str] = []
    for name, field_info in output_model.model_fields.items():
        limit = max_length_of(field_info.metadata)
        value = raw.get(name)
        if limit is None or not isinstance(value, list) or len(value) <= limit:
            continue
        bounded[name] = value[:limit]
        detail = f"{len(value)} items truncated to {limit}"
        results.append(
            GuardrailResult(
                check=GuardrailCheck.BOUNDS,
                outcome=GuardrailOutcome.WARNING,
                item_ref=name,
                detail=detail,
            )
        )
        feedback.append(f"{name}: has {len(value)} items; at most {limit} are allowed.")
    return GuardrailReport(
        output=bounded,
        results=tuple(results) or passed(GuardrailCheck.BOUNDS),
        feedback=tuple(feedback),
    )


def max_length_of(metadata: Iterable[object]) -> int | None:
    return next((meta.max_length for meta in metadata if hasattr(meta, "max_length")), None)
