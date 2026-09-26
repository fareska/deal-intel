"""Checks that run when a brief is rendered: customer-facing language lint and approval
consistency. Canary scanning lives in `guardrails.canaries`.

Approval consistency follows plan change C9: an item asserting approval in its own words is
withheld from the brief rather than failing the run; only an executive summary left too short
to stand fails it.
"""

from collections.abc import Collection, Sequence
from dataclasses import dataclass

from deal_intel.contracts.agents.common import AgentName
from deal_intel.contracts.agents.negotiation_strategy import MIN_SUMMARY_SENTENCES, StrategyOutput
from deal_intel.contracts.guardrails import GuardrailCheck, GuardrailOutcome, GuardrailResult
from deal_intel.contracts.runs import AnalysisOutputs, GuardrailsOutput, RunErrorCode
from deal_intel.guardrails.canaries import CanaryHit
from deal_intel.guardrails.validators import (
    EvidenceIndex,
    cited_fields,
    passed,
    review_approval_wording,
)
from deal_intel.guardrails.wording import concession_phrases, internal_workflow_terms

WITHHELD_CUSTOMER_TEXT = "[customer-facing language withheld pending approval]"
# A conflict reports both claims as found, and one side may be a claimed approval.
CONSISTENCY_EXEMPT_FIELDS: frozenset[str] = frozenset({"conflicts"})
EXECUTIVE_SUMMARY_FIELD = "executive_summary"
ITEM_REF_SEPARATOR = "."


class GuardrailFailure(RuntimeError):
    """`hits` carries canary kinds and hashes for a leak, never the canary values."""

    def __init__(self, error_code: RunErrorCode, hits: Sequence[CanaryHit] = ()) -> None:
        super().__init__(error_code.value)
        self.error_code = error_code
        self.hits = tuple(hits)


@dataclass(frozen=True)
class LintResult:
    text: str
    results: tuple[GuardrailResult, ...]


def item_ref(agent: AgentName, field_ref: str) -> str:
    return f"{agent.value}{ITEM_REF_SEPARATOR}{field_ref}"


def indexed_ref(agent: AgentName, field: str, index: int) -> str:
    return item_ref(agent, f"{field}[{index}]")


def lint_customer_facing(text: str, approved: bool, ref: str) -> LintResult:
    """Suppresses the whole text, not the phrase, so no half-sentence reaches a customer. An
    approval permits a concession; it never permits an internal detail."""
    leaked = internal_workflow_terms(text)
    if leaked:
        return withheld(GuardrailCheck.CUSTOMER_FACING_LEAK, ref, leaked)
    concessions = concession_phrases(text)
    if concessions and not approved:
        return withheld(GuardrailCheck.LANGUAGE_LINT, ref, concessions)
    return LintResult(text=text, results=())


def withheld(check: GuardrailCheck, ref: str, phrases: Sequence[str]) -> LintResult:
    result = GuardrailResult(
        check=check,
        outcome=GuardrailOutcome.WARNING,
        item_ref=ref,
        detail=", ".join(dict.fromkeys(phrases)),
    )
    return LintResult(text=WITHHELD_CUSTOMER_TEXT, results=(result,))


def review_approval_consistency(
    analysis: AnalysisOutputs, evidence: EvidenceIndex
) -> GuardrailsOutput:
    results: list[GuardrailResult] = []
    withheld_refs: list[str] = []
    for run in analysis.agent_runs():
        for cited in cited_fields(run.output):
            if cited.name in CONSISTENCY_EXEMPT_FIELDS:
                continue
            for field_ref, item in zip(cited.item_refs(), cited.items, strict=True):
                review = review_approval_wording(item, evidence)
                if review.item is not None:
                    continue
                ref = item_ref(run.agent_name, field_ref)
                withheld_refs.append(ref)
                results.extend(
                    GuardrailResult(
                        check=GuardrailCheck.APPROVAL_CONSISTENCY,
                        outcome=GuardrailOutcome.DROPPED,
                        item_ref=ref,
                        detail=problem.detail,
                    )
                    for problem in review.problems
                )
    require_salvageable_summary(analysis.strategy.output, withheld_refs)
    return GuardrailsOutput(
        results=results or list(passed(GuardrailCheck.APPROVAL_CONSISTENCY)),
        withheld_item_refs=withheld_refs,
    )


def require_salvageable_summary(strategy: StrategyOutput, withheld_refs: Collection[str]) -> None:
    if strategy.no_evidence:
        return
    kept = [
        index
        for index in range(len(strategy.executive_summary))
        if indexed_ref(AgentName.NEGOTIATION_STRATEGY, EXECUTIVE_SUMMARY_FIELD, index)
        not in withheld_refs
    ]
    if len(kept) < MIN_SUMMARY_SENTENCES:
        raise GuardrailFailure(RunErrorCode.APPROVAL_ASSERTION)
