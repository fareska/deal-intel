from deal_intel.agents.base import AgentRuntime, AgentSpec, run_agent
from deal_intel.agents.tools import EvidenceToolName
from deal_intel.contracts.access import SourceType
from deal_intel.contracts.agents.agent_run import AgentRun
from deal_intel.contracts.agents.common import AgentName
from deal_intel.contracts.agents.conversation_intelligence import ConversationFindings
from deal_intel.contracts.evidence import PackBuild
from deal_intel.contracts.llm import ModelRole
from deal_intel.guardrails.validators import FINDING_CHECKS

SPEC = AgentSpec[ConversationFindings](
    name=AgentName.CONVERSATION_INTELLIGENCE,
    output_model=ConversationFindings,
    model_role=ModelRole.EXTRACTION,
    source_types=frozenset({SourceType.GONG, SourceType.SLACK}),
    budget_tokens=lambda settings: settings.budget_conversation_intelligence_tokens,
    queries=(
        "objections pricing discount budget",
        "competitor alternative vendor",
        "next steps deadline timeline",
        "urgency board deadline renewal",
        "commitments agreed owner",
    ),
    max_tokens=4_000,
    empty_output=ConversationFindings.empty,
    validators=FINDING_CHECKS,
    tools=(EvidenceToolName.SEARCH_EVIDENCE, EvidenceToolName.GET_EVIDENCE),
)


def run_conversation_intelligence(
    runtime: AgentRuntime, pack_build: PackBuild | None = None
) -> AgentRun[ConversationFindings]:
    return run_agent(SPEC, runtime, pack_build=pack_build)
