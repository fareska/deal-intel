"""Evaluation metrics over stored runs, briefs, approvals, and guardrail results."""

from collections import Counter
from collections.abc import Mapping, Sequence
from decimal import Decimal
from statistics import fmean

from sqlalchemy.orm import Session

from deal_intel.contracts.approvals import PolicyOutput, RuleId, SubjectKind
from deal_intel.contracts.brief import SECTION_HEADINGS, Brief
from deal_intel.contracts.evidence import PackChunk
from deal_intel.contracts.guardrails import GuardrailOutcome
from deal_intel.contracts.runs import RetrieveOutput, RunRecord, RunState, StageName
from deal_intel.evaluation.goldens import (
    SECTION_FIELDS,
    ExpectedRules,
    load_expected_rules,
    section_is_populated,
)
from deal_intel.evaluation.report import MetricsReport
from deal_intel.evaluation.scenarios import DENIED_PAIR, DemoPair
from deal_intel.guardrails.canaries import denial_leaks
from deal_intel.guardrails.text import extract_figures
from deal_intel.orchestration.outputs import StageOutputMissing, optional_output, stored_guardrails
from deal_intel.orchestration.persistence import get_run, latest_outputs
from deal_intel.rendering.brief import latest_brief
from deal_intel.retrieval.ingest import latest_snapshot_id

PRICING_SUBJECT_PREFIX = f"{SubjectKind.PRICING.value}:"
EMPTY_RATE = 1.0


class ScenarioScore(MetricsReport):
    """One scenario's contribution; rates on a denied run without a brief are 1.0 when N/A."""


def metrics_report(
    session: Session,
    pairs: Sequence[tuple[DemoPair, str]],
    expected_rules: Mapping[str, ExpectedRules] | None = None,
) -> MetricsReport:
    rules = expected_rules if expected_rules is not None else load_expected_rules()
    scores = [scenario_score(session, pair, run_id, rules) for pair, run_id in pairs]
    return combine_scores(scores)


def scenario_score(
    session: Session,
    pair: DemoPair,
    run_id: str,
    expected_rules: Mapping[str, ExpectedRules],
) -> ScenarioScore:
    record = get_run(session, run_id)
    outputs = latest_outputs(session, run_id)
    brief_row = latest_brief(session, run_id)
    brief = None if brief_row is None else Brief.model_validate(brief_row.json)
    retrieval = optional_output(outputs, StageName.RETRIEVE, RetrieveOutput)
    policy = optional_output(outputs, StageName.POLICY, PolicyOutput)
    pack_chunks = pack_chunk_index(retrieval)
    cited_ids = brief_cited_ids(brief) if brief is not None else []
    drops = guardrail_drops(session, run_id)
    return ScenarioScore(
        citation_validity_rate=citation_validity(cited_ids, pack_chunks),
        grounded_number_rate=grounded_number_rate(brief, pack_chunks),
        section_completeness=section_completeness(brief),
        approval_routing_accuracy=routing_accuracy(pair, policy, expected_rules),
        denial_correctness=denial_correctness(session, pair, record, retrieval),
        degraded_rate=1.0 if record.degraded else 0.0,
        mean_cost_usd=record.cost_usd,
        mean_input_tokens=record.input_tokens,
        mean_output_tokens=sum(row.output_tokens for row in outputs.values()),
        guardrail_drops_by_check=drops,
        scenarios=1,
    )


def combine_scores(scores: Sequence[ScenarioScore]) -> MetricsReport:
    if not scores:
        return MetricsReport(
            citation_validity_rate=EMPTY_RATE,
            grounded_number_rate=EMPTY_RATE,
            section_completeness=EMPTY_RATE,
            approval_routing_accuracy=EMPTY_RATE,
            denial_correctness=EMPTY_RATE,
            degraded_rate=0.0,
            mean_cost_usd=Decimal(0),
            mean_input_tokens=0,
            mean_output_tokens=0,
            guardrail_drops_by_check={},
            scenarios=0,
        )
    drops: Counter[str] = Counter()
    for score in scores:
        drops.update(score.guardrail_drops_by_check)
    return MetricsReport(
        citation_validity_rate=fmean(score.citation_validity_rate for score in scores),
        grounded_number_rate=fmean(score.grounded_number_rate for score in scores),
        section_completeness=fmean(score.section_completeness for score in scores),
        approval_routing_accuracy=fmean(score.approval_routing_accuracy for score in scores),
        denial_correctness=fmean(score.denial_correctness for score in scores),
        degraded_rate=fmean(score.degraded_rate for score in scores),
        mean_cost_usd=sum((score.mean_cost_usd for score in scores), Decimal(0)) / len(scores),
        mean_input_tokens=round(fmean(score.mean_input_tokens for score in scores)),
        mean_output_tokens=round(fmean(score.mean_output_tokens for score in scores)),
        guardrail_drops_by_check=dict(sorted(drops.items())),
        scenarios=len(scores),
    )


def citation_validity(cited_ids: Sequence[str], pack_chunks: Mapping[str, PackChunk]) -> float:
    if not cited_ids:
        return EMPTY_RATE
    known = [chunk_id for chunk_id in cited_ids if chunk_id in pack_chunks]
    return len(known) / len(cited_ids)


def grounded_number_rate(brief: Brief | None, pack_chunks: Mapping[str, PackChunk]) -> float:
    if brief is None:
        return EMPTY_RATE
    stated = extract_figures(brief.model_dump_json())
    if not stated:
        return EMPTY_RATE
    cited = brief_cited_ids(brief)
    evidence = " ".join(pack_chunks[chunk_id].text for chunk_id in cited if chunk_id in pack_chunks)
    found = extract_figures(evidence)
    grounded = [figure for figure in stated if any(figure.matches(seen) for seen in found)]
    return len(grounded) / len(stated)


def section_completeness(brief: Brief | None) -> float:
    if brief is None:
        return EMPTY_RATE
    filled = sum(1 for field in SECTION_FIELDS if section_is_populated(getattr(brief, field)))
    return filled / len(SECTION_HEADINGS)


def routing_accuracy(
    pair: DemoPair,
    policy: PolicyOutput | None,
    expected_rules: Mapping[str, ExpectedRules],
) -> float:
    if pair == DENIED_PAIR:
        return EMPTY_RATE
    expected = set(expected_rules[pair.opportunity_id].pricing_note_rules)
    return EMPTY_RATE if pricing_note_rules(policy) == expected else 0.0


def pricing_note_rules(policy: PolicyOutput | None) -> set[RuleId]:
    if policy is None:
        return set()
    return {
        rule
        for recommendation_id, rules in policy.fired_rules.items()
        if recommendation_id.startswith(PRICING_SUBJECT_PREFIX)
        for rule in rules
    }


def denial_correctness(
    session: Session,
    pair: DemoPair,
    record: RunRecord,
    retrieval: RetrieveOutput | None,
) -> float:
    if pair != DENIED_PAIR:
        return EMPTY_RATE
    packs = (
        0
        if retrieval is None
        else sum(len(build.pack.chunks) for build in retrieval.packs.values())
    )
    snapshot_id = latest_snapshot_id(session)
    leaks = (
        []
        if snapshot_id is None
        else denial_leaks(session, record.run_id, [pair.user_id, pair.opportunity_id], [])
    )
    correct = record.state is RunState.DENIED and packs == 0 and not leaks
    return EMPTY_RATE if correct else 0.0


def pack_chunk_index(retrieval: RetrieveOutput | None) -> dict[str, PackChunk]:
    if retrieval is None:
        return {}
    return {
        chunk.chunk_id: chunk for build in retrieval.packs.values() for chunk in build.pack.chunks
    }


def brief_cited_ids(brief: Brief) -> list[str]:
    return [entry.chunk_id for entry in brief.source_evidence.entries]


def guardrail_drops(session: Session, run_id: str) -> dict[str, int]:
    outputs = latest_outputs(session, run_id)
    try:
        stored = stored_guardrails(outputs)
    except StageOutputMissing:
        stored = None
    results = [] if stored is None else stored.results
    counts = Counter(
        result.check.value for result in results if result.outcome is GuardrailOutcome.DROPPED
    )
    return dict(sorted(counts.items()))
