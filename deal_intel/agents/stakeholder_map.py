"""Single call, no tools: the committee is read from a fixed pack so every name it lists can be
verified against that pack. Vendor-side people are refused by the output contract, and invented
names are dropped by the name check."""

from deal_intel.agents.base import AgentRuntime, AgentSpec, run_agent
from deal_intel.contracts.access import SourceType
from deal_intel.contracts.agents.agent_run import AgentRun
from deal_intel.contracts.agents.common import AgentName
from deal_intel.contracts.agents.stakeholder_map import StakeholderMap
from deal_intel.contracts.evidence import PackBuild
from deal_intel.contracts.llm import ModelRole
from deal_intel.guardrails.validators import STAKEHOLDER_CHECKS

SPEC = AgentSpec[StakeholderMap](
    name=AgentName.STAKEHOLDER_MAP,
    output_model=StakeholderMap,
    model_role=ModelRole.EXTRACTION,
    source_types=frozenset({SourceType.SALESFORCE, SourceType.GONG, SourceType.SLACK}),
    budget_tokens=lambda settings: settings.budget_stakeholder_map_tokens,
    queries=(
        "decision maker sponsor approver",
        "legal counsel procurement",
        "security architect technical evaluation",
        "skeptical pushback concern",
    ),
    max_tokens=3_000,
    empty_output=StakeholderMap.empty,
    validators=STAKEHOLDER_CHECKS,
)


def run_stakeholder_map(
    runtime: AgentRuntime, pack_build: PackBuild | None = None
) -> AgentRun[StakeholderMap]:
    return run_agent(SPEC, runtime, pack_build=pack_build)
