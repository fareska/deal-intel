"""Approved customer-facing language, from one template per pricing rule. Only code writes
customer-facing concession text; the model never does (policy rule 6)."""

from collections.abc import Mapping, Sequence
from functools import lru_cache

from jinja2 import Environment, PackageLoader, StrictUndefined
from pydantic import JsonValue

from deal_intel.contracts.agents.negotiation_strategy import ProposedValues
from deal_intel.contracts.approvals import RuleId
from deal_intel.retrieval.chunkers.base import plain_number

TEMPLATE_PACKAGE = "deal_intel.rendering"
TEMPLATE_DIRECTORY = "approved_language"
TEMPLATE_SUFFIX = ".md.j2"
TEMPLATED_RULES: tuple[RuleId, ...] = (RuleId.R1, RuleId.R2, RuleId.R3)


@lru_cache(maxsize=1)
def template_environment() -> Environment:
    # Markdown output from numeric values only; there is no HTML to escape.
    return Environment(  # noqa: S701
        loader=PackageLoader(TEMPLATE_PACKAGE, TEMPLATE_DIRECTORY),
        undefined=StrictUndefined,
        keep_trailing_newline=False,
    )


def approved_customer_language(
    rule_ids: Sequence[RuleId], proposed_values: Mapping[str, JsonValue]
) -> str | None:
    templated = [rule_id for rule_id in TEMPLATED_RULES if rule_id in rule_ids]
    if not templated:
        return None
    values = template_values(ProposedValues.model_validate(proposed_values))
    return " ".join(render_rule(rule_id, values) for rule_id in templated)


def render_rule(rule_id: RuleId, values: Mapping[str, str]) -> str:
    template = template_environment().get_template(f"{rule_id.value}{TEMPLATE_SUFFIX}")
    return template.render(**values).strip()


def template_values(values: ProposedValues) -> dict[str, str]:
    numbers = {"discount_pct": values.discount_pct, "uplift_pct": values.uplift_pct}
    return {name: plain_number(value) for name, value in numbers.items() if value is not None}
