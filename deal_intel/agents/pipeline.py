"""The snapshot and the three agents, behind one seam the M4 runner and the fixture recorder share.

`AgentSuite` is the only way either reaches an agent, so a recording always replays the way it
was made, and tests can swap in hand-authored outputs without touching the orchestration.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy.orm import Session

from deal_intel.agents import conversation_intelligence, negotiation_strategy, stakeholder_map
from deal_intel.agents.base import AgentRuntime, AgentSpec, build_agent_pack
from deal_intel.agents.deal_snapshot import build_deal_snapshot
from deal_intel.contracts.agents.agent_run import AgentRun
from deal_intel.contracts.agents.common import AgentName
from deal_intel.contracts.agents.conversation_intelligence import ConversationFindings
from deal_intel.contracts.agents.deal_snapshot import DealSnapshot, SnapshotBuild
from deal_intel.contracts.agents.negotiation_strategy import StrategyOutput
from deal_intel.contracts.agents.stakeholder_map import StakeholderMap
from deal_intel.contracts.evidence import PackBuild
from deal_intel.retrieval.retriever import ScopedRetriever

LLM_AGENT_SPECS: Mapping[AgentName, AgentSpec] = {
    AgentName.CONVERSATION_INTELLIGENCE: conversation_intelligence.SPEC,
    AgentName.STAKEHOLDER_MAP: stakeholder_map.SPEC,
    AgentName.NEGOTIATION_STRATEGY: negotiation_strategy.SPEC,
}


class AgentSuite(Protocol):
    def deal_snapshot(self, session: Session, retriever: ScopedRetriever) -> SnapshotBuild: ...

    def conversation_intelligence(
        self, runtime: AgentRuntime, pack_build: PackBuild | None
    ) -> AgentRun[ConversationFindings]: ...

    def stakeholder_map(
        self, runtime: AgentRuntime, pack_build: PackBuild | None
    ) -> AgentRun[StakeholderMap]: ...

    def negotiation_strategy(
        self,
        runtime: AgentRuntime,
        snapshot: DealSnapshot,
        findings: ConversationFindings | None,
        stakeholders: StakeholderMap | None,
        pack_build: PackBuild | None,
    ) -> AgentRun[StrategyOutput]: ...


class LiveAgents:
    def deal_snapshot(self, session: Session, retriever: ScopedRetriever) -> SnapshotBuild:
        return build_deal_snapshot(session, retriever)

    def conversation_intelligence(
        self, runtime: AgentRuntime, pack_build: PackBuild | None
    ) -> AgentRun[ConversationFindings]:
        return conversation_intelligence.run_conversation_intelligence(runtime, pack_build)

    def stakeholder_map(
        self, runtime: AgentRuntime, pack_build: PackBuild | None
    ) -> AgentRun[StakeholderMap]:
        return stakeholder_map.run_stakeholder_map(runtime, pack_build)

    def negotiation_strategy(
        self,
        runtime: AgentRuntime,
        snapshot: DealSnapshot,
        findings: ConversationFindings | None,
        stakeholders: StakeholderMap | None,
        pack_build: PackBuild | None,
    ) -> AgentRun[StrategyOutput]:
        return negotiation_strategy.run_negotiation_strategy(
            runtime, snapshot, findings, stakeholders, pack_build
        )


LIVE_AGENTS = LiveAgents()


def build_agent_packs(runtime: AgentRuntime) -> dict[AgentName, PackBuild]:
    """Every LLM agent's pack, built before any agent runs so a run can persist them first."""
    return {
        name: build_agent_pack(spec, runtime.retriever, runtime.settings, runtime.tracer)
        for name, spec in LLM_AGENT_SPECS.items()
    }


@dataclass(frozen=True)
class AgentOutputs:
    snapshot: DealSnapshot
    findings: AgentRun[ConversationFindings]
    stakeholders: AgentRun[StakeholderMap]
    strategy: AgentRun[StrategyOutput]


def run_all_agents(
    session: Session, runtime: AgentRuntime, agents: AgentSuite = LIVE_AGENTS
) -> AgentOutputs:
    snapshot = agents.deal_snapshot(session, runtime.retriever).snapshot
    findings = agents.conversation_intelligence(runtime, None)
    stakeholders = agents.stakeholder_map(runtime, None)
    strategy = agents.negotiation_strategy(
        runtime, snapshot, findings.output, stakeholders.output, None
    )
    return AgentOutputs(
        snapshot=snapshot, findings=findings, stakeholders=stakeholders, strategy=strategy
    )
