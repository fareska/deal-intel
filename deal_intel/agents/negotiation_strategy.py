"""The synthesis agent. Approval-assertion and customer-facing-leak wording are validators, so they
go through the retry-with-feedback policy like any other guardrail finding; a degraded input is
named in `review_warnings` by the harness whenever the model leaves it out."""

from collections.abc import Sequence

from deal_intel.agents.base import AgentRuntime, AgentSpec, run_agent
from deal_intel.agents.strategy_context import build_strategy_context
from deal_intel.agents.tools import EvidenceToolName
from deal_intel.contracts.access import SourceType
from deal_intel.contracts.agents.agent_run import AgentRun
from deal_intel.contracts.agents.common import MAX_LIST_ITEMS, AgentName
from deal_intel.contracts.agents.conversation_intelligence import ConversationFindings
from deal_intel.contracts.agents.deal_snapshot import DealSnapshot
from deal_intel.contracts.agents.negotiation_strategy import StrategyOutput
from deal_intel.contracts.agents.stakeholder_map import StakeholderMap
from deal_intel.contracts.evidence import PackBuild
from deal_intel.contracts.guardrails import GuardrailCheck, GuardrailOutcome, GuardrailResult
from deal_intel.contracts.llm import ModelRole
from deal_intel.guardrails.validators import STRATEGY_CHECKS

REVIEW_WARNINGS_FIELD = "review_warnings"
DEGRADED_WARNING = "{name} unavailable; brief generated without it"

SPEC = AgentSpec[StrategyOutput](
    name=AgentName.NEGOTIATION_STRATEGY,
    output_model=StrategyOutput,
    model_role=ModelRole.STRATEGY,
    # Every type; the retriever intersects them with the scope.
    source_types=frozenset(SourceType),
    budget_tokens=lambda settings: settings.budget_negotiation_strategy_tokens,
    queries=(
        "discount concession approval",
        "risk objection blocker",
        "close date timeline next step",
        "legal liability data retention",
    ),
    # Adaptive thinking spends from the same allowance as the answer.
    max_tokens=16_000,
    empty_output=StrategyOutput.empty,
    validators=STRATEGY_CHECKS,
    tools=(EvidenceToolName.SEARCH_EVIDENCE, EvidenceToolName.GET_EVIDENCE),
)


def run_negotiation_strategy(
    runtime: AgentRuntime,
    snapshot: DealSnapshot,
    findings: ConversationFindings | None,
    stakeholders: StakeholderMap | None,
    pack_build: PackBuild | None = None,
) -> AgentRun[StrategyOutput]:
    """`None` for a subagent output means that subagent failed and the input is degraded."""
    context = build_strategy_context(
        snapshot, findings, stakeholders, runtime.retriever.scope, runtime.settings
    )
    run = run_agent(SPEC, runtime, pack_build=pack_build, task_context=context)
    return with_degraded_warnings(run, context.degraded_inputs)


def with_degraded_warnings(
    run: AgentRun[StrategyOutput], degraded_inputs: Sequence[AgentName]
) -> AgentRun[StrategyOutput]:
    unnamed = [
        name
        for name in degraded_inputs
        if not any(name.value in warning for warning in run.output.review_warnings)
    ]
    if not unnamed:
        return run
    added = [DEGRADED_WARNING.format(name=name.value) for name in unnamed]
    # Added first so a full list gives up one of the model's own warnings instead.
    warnings = [*added, *run.output.review_warnings][:MAX_LIST_ITEMS]
    output = run.output.model_copy(update={REVIEW_WARNINGS_FIELD: warnings})
    return run.amended(output, [degraded_record(name) for name in unnamed])


def degraded_record(name: AgentName) -> GuardrailResult:
    return GuardrailResult(
        check=GuardrailCheck.DEGRADED_INPUTS,
        outcome=GuardrailOutcome.MODIFIED,
        item_ref=REVIEW_WARNINGS_FIELD,
        detail=f"added the missing {name.value} warning",
    )
