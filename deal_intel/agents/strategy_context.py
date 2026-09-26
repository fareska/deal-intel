"""Pure assembly of the negotiation strategy agent's task context from upstream outputs."""

from deal_intel.agents.scope_guard import ScopeViolation
from deal_intel.config import Settings
from deal_intel.contracts.access import AccessScope
from deal_intel.contracts.agents.conversation_intelligence import ConversationFindings
from deal_intel.contracts.agents.deal_snapshot import DealSnapshot
from deal_intel.contracts.agents.negotiation_strategy import (
    LEGAL_REVIEW_TAGS,
    PolicySummary,
    PolicyThresholds,
    ScopeFlags,
    StrategyContext,
    missing_subagents,
)
from deal_intel.contracts.agents.stakeholder_map import StakeholderMap


def build_strategy_context(
    snapshot: DealSnapshot,
    findings: ConversationFindings | None,
    stakeholders: StakeholderMap | None,
    scope: AccessScope,
    settings: Settings,
) -> StrategyContext:
    """`None` for a subagent output means that subagent failed; it is named in
    `degraded_inputs` so the strategy agent must say what the brief was built without."""
    if snapshot.opportunity.record.opportunity_id != scope.opportunity_id:
        raise ScopeViolation("snapshot was built for a different opportunity")
    return StrategyContext(
        snapshot=snapshot,
        findings=findings,
        stakeholders=stakeholders,
        policy=policy_summary_for(scope, settings),
        scope=scope_flags_of(scope),
        degraded_inputs=missing_subagents(findings, stakeholders),
    )


def policy_summary_for(scope: AccessScope, settings: Settings) -> PolicySummary | None:
    """Users without `policies` access get no policy content at all, thresholds included."""
    if not scope.policies_allowed:
        return None
    thresholds = {name: getattr(settings, name) for name in PolicyThresholds.model_fields}
    return PolicySummary(**thresholds, legal_review_tags=sorted(LEGAL_REVIEW_TAGS))


def scope_flags_of(scope: AccessScope) -> ScopeFlags:
    return ScopeFlags(
        source_types=sorted(scope.source_types),
        max_access_level=scope.max_access_level,
        policies_allowed=scope.policies_allowed,
        can_request_approval=scope.can_request_approval,
    )
