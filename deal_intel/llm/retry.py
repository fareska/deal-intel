"""The retry policy: schema errors and guardrail findings go back to the model as feedback, up to
`retries` times. After that, schema errors fail the call; guardrail findings are resolved by
keeping the cleaned output (offending items dropped), provided it still meets its contract."""

from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum

from pydantic import BaseModel, ValidationError

from deal_intel.contracts.evidence import PackChunk
from deal_intel.contracts.guardrails import (
    OUTPUT_ITEM_REF,
    GuardrailCheck,
    GuardrailOutcome,
    GuardrailReport,
    GuardrailResult,
    OutputCheck,
)
from deal_intel.contracts.tracing import SpanAttribute, SpanKind
from deal_intel.llm.errors import OutputNotSalvageable, SchemaValidationError
from deal_intel.llm.parsing import ParsedOutput, describe_validation_error, parse_output
from deal_intel.llm.tool_loop import ToolLoop
from deal_intel.observability.tracing import Tracer

GUARDRAIL_SPAN_NAME = "guardrails"
FEEDBACK_INTRO = "Your previous output has these problems:"
FEEDBACK_OUTRO = "Return the complete corrected output in the same schema."
OUTCOME_ATTRIBUTES = {
    GuardrailOutcome.PASSED: SpanAttribute.GUARDRAIL_PASSED,
    GuardrailOutcome.DROPPED: SpanAttribute.GUARDRAIL_DROPPED,
    GuardrailOutcome.MODIFIED: SpanAttribute.GUARDRAIL_MODIFIED,
    GuardrailOutcome.WARNING: SpanAttribute.GUARDRAIL_WARNINGS,
}


class RetryReason(StrEnum):
    SCHEMA = "schema"
    GUARDRAILS = "guardrails"


@dataclass(frozen=True)
class CheckedOutput[OutputT: BaseModel]:
    output: OutputT
    raw_text: str
    results: tuple[GuardrailResult, ...]
    attempts: int


def complete_with_feedback[OutputT: BaseModel](
    loop: ToolLoop,
    output_model: type[OutputT],
    check: OutputCheck[OutputT] | None,
    retries: int,
    tracer: Tracer,
) -> CheckedOutput[OutputT]:
    retry_records: list[GuardrailResult] = []
    last_attempt = retries + 1
    for attempt in range(1, last_attempt + 1):
        raw_text = loop.next_output().output_text
        try:
            parsed = parse_output(output_model, raw_text)
        except SchemaValidationError as error:
            if attempt == last_attempt:
                raise
            retry_records.append(retry_record(attempt, RetryReason.SCHEMA, 1))
            loop.append_user_text(feedback_message([str(error)]))
            continue
        report = run_check(check, parsed.output, loop.tool_chunks, tracer, attempt)
        if report.passed:
            return checked(parsed, report, raw_text, retry_records, attempt)
        if attempt == last_attempt:
            salvaged = salvage(output_model, report, check, loop.tool_chunks, tracer, attempt)
            return checked(parsed, salvaged, raw_text, retry_records, attempt)
        retry_records.append(retry_record(attempt, RetryReason.GUARDRAILS, len(report.feedback)))
        loop.append_user_text(feedback_message(report.feedback))
    raise AssertionError("unreachable: the last attempt returns or raises")


def run_check[OutputT: BaseModel](
    check: OutputCheck[OutputT] | None,
    output: OutputT,
    tool_chunks: Sequence[PackChunk],
    tracer: Tracer,
    attempt: int,
) -> GuardrailReport[OutputT]:
    if check is None:
        return GuardrailReport(output=output, results=(), feedback=())
    with tracer.span(
        GUARDRAIL_SPAN_NAME, SpanKind.GUARDRAIL, {SpanAttribute.ATTEMPT: attempt}
    ) as span:
        report = check(output, tool_chunks)
        span.set_attributes(
            {**outcome_counts(report.results), SpanAttribute.FEEDBACK_COUNT: len(report.feedback)}
        )
    return report


def salvage[OutputT: BaseModel](
    output_model: type[OutputT],
    report: GuardrailReport[OutputT],
    check: OutputCheck[OutputT] | None,
    tool_chunks: Sequence[PackChunk],
    tracer: Tracer,
    attempt: int,
) -> GuardrailReport[OutputT]:
    """Keeps the cleaned output once it passes its contract and the checks again, and adds a
    record saying the retries ran out."""
    try:
        revalidated = output_model.model_validate(report.output.model_dump())
    except ValidationError as error:
        raise OutputNotSalvageable(
            f"dropping the offending items breaks the output contract: "
            f"{describe_validation_error(error)}"
        ) from error
    if not run_check(check, revalidated, tool_chunks, tracer, attempt).passed:
        raise OutputNotSalvageable("the cleaned output still fails its guardrails")
    exhausted = GuardrailResult(
        check=GuardrailCheck.RETRY,
        outcome=GuardrailOutcome.DROPPED,
        item_ref=OUTPUT_ITEM_REF,
        detail=f"retries exhausted after {attempt} attempts; offending items removed",
    )
    return GuardrailReport(
        output=revalidated, results=(*report.results, exhausted), feedback=report.feedback
    )


def checked[OutputT: BaseModel](
    parsed: ParsedOutput[OutputT],
    report: GuardrailReport[OutputT],
    raw_text: str | None,
    retry_records: Sequence[GuardrailResult],
    attempt: int,
) -> CheckedOutput[OutputT]:
    return CheckedOutput(
        output=report.output,
        raw_text=raw_text or "",
        results=(*retry_records, *parsed.bound_results, *report.results),
        attempts=attempt,
    )


def retry_record(attempt: int, reason: RetryReason, issues: int) -> GuardrailResult:
    return GuardrailResult(
        check=GuardrailCheck.RETRY,
        outcome=GuardrailOutcome.RETRIED,
        item_ref=OUTPUT_ITEM_REF,
        detail=f"attempt {attempt}: {issues} {reason} issue(s) sent back as feedback",
    )


def feedback_message(problems: Iterable[str]) -> str:
    lines = [FEEDBACK_INTRO, *(f"- {problem}" for problem in problems), FEEDBACK_OUTRO]
    return "\n".join(lines)


def outcome_counts(results: Iterable[GuardrailResult]) -> dict[SpanAttribute, int]:
    counts = Counter(result.outcome for result in results)
    return {attribute: counts[outcome] for outcome, attribute in OUTCOME_ATTRIBUTES.items()}
