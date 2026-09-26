from decimal import Decimal

import pytest
from pydantic import ValidationError
from sqlalchemy.orm import Session

from deal_intel.agents.deal_snapshot import build_deal_snapshot
from deal_intel.agents.scope_guard import ScopeViolation
from deal_intel.agents.strategy_context import build_strategy_context
from deal_intel.config import Settings, get_settings
from deal_intel.contracts.access import AccessScope, Allowed, SourceType
from deal_intel.contracts.agents.common import AgentName
from deal_intel.contracts.agents.conversation_intelligence import ConversationFindings
from deal_intel.contracts.agents.deal_snapshot import DealSnapshot
from deal_intel.contracts.agents.negotiation_strategy import (
    LEGAL_REVIEW_TAGS,
    PolicySummary,
    PolicyThresholds,
    StrategyContext,
)
from deal_intel.contracts.agents.stakeholder_map import StakeholderMap
from deal_intel.permissions.gate import authorize
from deal_intel.retrieval.retriever import ScopedRetriever

CUSTOM_DEAL_DESK_THRESHOLD = Decimal("11.5")


def scope_for(session: Session, user_id: str, opportunity_id: str) -> AccessScope:
    result = authorize(session, user_id, opportunity_id)
    assert isinstance(result, Allowed)
    return result.scope


def snapshot_for(session: Session, scope: AccessScope) -> DealSnapshot:
    return build_deal_snapshot(session, ScopedRetriever(session, scope)).snapshot


def context_for(
    session: Session,
    user_id: str,
    opportunity_id: str,
    settings: Settings | None = None,
    findings: ConversationFindings | None = None,
    stakeholders: StakeholderMap | None = None,
) -> StrategyContext:
    scope = scope_for(session, user_id, opportunity_id)
    return build_strategy_context(
        snapshot_for(session, scope), findings, stakeholders, scope, settings or get_settings()
    )


def test_narrow_scope_gets_no_policy_summary(ingested_session: Session) -> None:
    context = context_for(
        ingested_session,
        "USR-5007",
        "OPP-1001",
        findings=ConversationFindings.empty(),
        stakeholders=StakeholderMap.empty(),
    )

    assert context.policy is None
    assert "policy_discount" not in context.model_dump_json()
    assert not context.scope.policies_allowed
    assert context.scope.source_types == [SourceType.GONG, SourceType.SALESFORCE]


def test_policy_summary_is_read_from_settings(ingested_session: Session) -> None:
    settings = get_settings().model_copy(
        update={"policy_discount_deal_desk_threshold_pct": CUSTOM_DEAL_DESK_THRESHOLD}
    )

    context = context_for(ingested_session, "USR-5001", "OPP-1001", settings)

    assert context.policy is not None
    assert context.policy.policy_discount_deal_desk_threshold_pct == CUSTOM_DEAL_DESK_THRESHOLD
    assert context.policy.policy_discount_sales_leader_threshold_pct == (
        settings.policy_discount_sales_leader_threshold_pct
    )
    assert set(context.policy.legal_review_tags) == LEGAL_REVIEW_TAGS


def test_policy_threshold_names_are_settings_names() -> None:
    assert set(PolicyThresholds.model_fields) <= set(Settings.model_fields)


@pytest.mark.parametrize(
    ("findings", "stakeholders", "degraded"),
    [
        (ConversationFindings.empty(), StakeholderMap.empty(), []),
        (None, StakeholderMap.empty(), [AgentName.CONVERSATION_INTELLIGENCE]),
        (ConversationFindings.empty(), None, [AgentName.STAKEHOLDER_MAP]),
        (None, None, [AgentName.CONVERSATION_INTELLIGENCE, AgentName.STAKEHOLDER_MAP]),
    ],
)
def test_missing_subagent_outputs_are_degraded_inputs(
    ingested_session: Session,
    findings: ConversationFindings | None,
    stakeholders: StakeholderMap | None,
    degraded: list[AgentName],
) -> None:
    context = context_for(
        ingested_session, "USR-5002", "OPP-1002", findings=findings, stakeholders=stakeholders
    )

    assert context.degraded_inputs == degraded


def test_contract_refuses_policy_without_policies_access(ingested_session: Session) -> None:
    narrow = context_for(ingested_session, "USR-5007", "OPP-1001")
    policy = PolicySummary(
        policy_discount_deal_desk_threshold_pct=Decimal(10),
        policy_discount_sales_leader_threshold_pct=Decimal(15),
        policy_renewal_uplift_floor_pct=Decimal(0),
        legal_review_tags=sorted(LEGAL_REVIEW_TAGS),
    )

    with pytest.raises(ValidationError, match="policies access"):
        StrategyContext.model_validate({**dict(narrow), "policy": policy})


def test_contract_refuses_unnamed_degraded_inputs(ingested_session: Session) -> None:
    context = context_for(ingested_session, "USR-5002", "OPP-1002")

    with pytest.raises(ValidationError, match="degraded_inputs"):
        StrategyContext.model_validate({**dict(context), "degraded_inputs": []})


def test_snapshot_of_another_opportunity_is_a_scope_violation(ingested_session: Session) -> None:
    owner = scope_for(ingested_session, "USR-5001", "OPP-1001")
    other = snapshot_for(ingested_session, scope_for(ingested_session, "USR-5002", "OPP-1002"))

    with pytest.raises(ScopeViolation):
        build_strategy_context(other, None, None, owner, get_settings())


def test_context_round_trips(ingested_session: Session) -> None:
    context = context_for(
        ingested_session,
        "USR-5003",
        "OPP-1003",
        findings=ConversationFindings.empty(),
        stakeholders=StakeholderMap.empty(),
    )

    assert StrategyContext.model_validate_json(context.model_dump_json()) == context
