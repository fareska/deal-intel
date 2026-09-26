"""The Deal Desk rules as data: each is a predicate over `RecommendationFacts`, the roles it
routes to, and its effect. Thresholds come from the same settings the strategy prompt shows."""

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from decimal import Decimal
from typing import Self

from deal_intel.config import Settings
from deal_intel.contracts.agents.negotiation_strategy import (
    LEGAL_REVIEW_TAGS,
    PRICING_SENSITIVITY_TAGS,
)
from deal_intel.contracts.approvals import ApproverRole, RuleEffect, RuleId
from deal_intel.policy.facts import RecommendationFacts


@dataclass(frozen=True)
class Thresholds:
    deal_desk_discount_pct: Decimal
    sales_leader_discount_pct: Decimal
    renewal_uplift_floor_pct: Decimal

    @classmethod
    def from_settings(cls, settings: Settings) -> Self:
        return cls(
            deal_desk_discount_pct=settings.policy_discount_deal_desk_threshold_pct,
            sales_leader_discount_pct=settings.policy_discount_sales_leader_threshold_pct,
            renewal_uplift_floor_pct=settings.policy_renewal_uplift_floor_pct,
        )


type Predicate = Callable[[RecommendationFacts, Thresholds], bool]


@dataclass(frozen=True)
class Rule:
    id: RuleId
    predicate: Predicate
    required_roles: tuple[ApproverRole, ...]
    effect: RuleEffect


def exceeds(value: Decimal | None, threshold: Decimal) -> bool:
    return value is not None and value > threshold


def discount_above_deal_desk(facts: RecommendationFacts, thresholds: Thresholds) -> bool:
    return exceeds(facts.discount_pct, thresholds.deal_desk_discount_pct)


def discount_above_sales_leader(facts: RecommendationFacts, thresholds: Thresholds) -> bool:
    return exceeds(facts.discount_pct, thresholds.sales_leader_discount_pct)


def uplift_below_floor(facts: RecommendationFacts, thresholds: Thresholds) -> bool:
    return facts.uplift_pct is not None and facts.uplift_pct < thresholds.renewal_uplift_floor_pct


def changes_liability_cap(facts: RecommendationFacts, _: Thresholds) -> bool:
    return facts.liability_cap_change


def touches_legal_terms(facts: RecommendationFacts, _: Thresholds) -> bool:
    return bool(facts.tags & LEGAL_REVIEW_TAGS)


def customer_facing_concession(facts: RecommendationFacts, _: Thresholds) -> bool:
    return facts.customer_facing and bool(facts.tags & PRICING_SENSITIVITY_TAGS)


def needs_human_review(facts: RecommendationFacts, _: Thresholds) -> bool:
    return facts.low_confidence or facts.has_conflict or facts.missing_source_data


RULES: tuple[Rule, ...] = (
    Rule(RuleId.R1, discount_above_deal_desk, (ApproverRole.DEAL_DESK,), RuleEffect.APPROVAL),
    Rule(
        RuleId.R2,
        discount_above_sales_leader,
        (ApproverRole.DEAL_DESK, ApproverRole.SALES_LEADER),
        RuleEffect.APPROVAL,
    ),
    Rule(RuleId.R3, uplift_below_floor, (ApproverRole.DEAL_DESK,), RuleEffect.APPROVAL),
    Rule(
        RuleId.R4,
        changes_liability_cap,
        (ApproverRole.LEGAL,),
        RuleEffect.APPROVAL_NO_CUSTOMER_LANGUAGE,
    ),
    Rule(
        RuleId.R5,
        touches_legal_terms,
        (ApproverRole.LEGAL,),
        RuleEffect.APPROVAL_EXTERNAL_LANGUAGE_WITHHELD,
    ),
    # Enforced at render time by the customer-facing language lint; no approval row.
    Rule(RuleId.R6, customer_facing_concession, (), RuleEffect.SUPPRESS_UNTIL_APPROVED),
    Rule(RuleId.R7, needs_human_review, (ApproverRole.HUMAN_REVIEWER,), RuleEffect.REVIEW),
)


RULES_BY_ID: dict[RuleId, Rule] = {rule.id: rule for rule in RULES}


def fired_rules(facts: RecommendationFacts, thresholds: Thresholds) -> list[Rule]:
    return [rule for rule in RULES if rule.predicate(facts, thresholds)]


def routes_to_approver(rule_ids: Iterable[RuleId]) -> bool:
    return any(RULES_BY_ID[rule_id].required_roles for rule_id in rule_ids)
