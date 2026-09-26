"""Run the evaluation scenarios through the existing runner."""

from collections.abc import Callable
from datetime import datetime

from sqlalchemy.orm import Session, sessionmaker

from deal_intel.agents.pipeline import LIVE_AGENTS, AgentSuite
from deal_intel.config import Settings
from deal_intel.contracts.runs import RunRequest
from deal_intel.evaluation.scenarios import EVAL_PAIRS, DemoPair
from deal_intel.llm.client import LlmClient
from deal_intel.observability.tracing import Tracer, utc_now
from deal_intel.orchestration.persistence import create_run
from deal_intel.orchestration.runner import Runner, RunnerDeps


def run_eval_pair(
    session_factory: sessionmaker[Session],
    pair: DemoPair,
    llm: LlmClient,
    settings: Settings,
    tracer: Tracer,
    agents: AgentSuite = LIVE_AGENTS,
    clock: Callable[[], datetime] = utc_now,
) -> str:
    request = RunRequest(user_id=pair.user_id, opportunity_id=pair.opportunity_id, fresh=False)
    with session_factory.begin() as session:
        run_id = create_run(session, request, clock()).run_id
    Runner(
        RunnerDeps(
            session_factory=session_factory,
            llm=llm,
            tracer=tracer,
            settings=settings,
            agents=agents,
            clock=clock,
        )
    ).run(run_id)
    return run_id


def run_eval_pairs(
    session_factory: sessionmaker[Session],
    llm: LlmClient,
    settings: Settings,
    tracer: Tracer,
    agents: AgentSuite = LIVE_AGENTS,
    pairs: tuple[DemoPair, ...] = EVAL_PAIRS,
    clock: Callable[[], datetime] = utc_now,
) -> list[tuple[DemoPair, str]]:
    return [
        (pair, run_eval_pair(session_factory, pair, llm, settings, tracer, agents, clock))
        for pair in pairs
    ]
