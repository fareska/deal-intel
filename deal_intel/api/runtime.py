"""The process-wide runtime the API lifespan owns: runner, executor, and expiry sweep."""

import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session, sessionmaker

from deal_intel.agents.pipeline import LIVE_AGENTS, AgentSuite
from deal_intel.config import Settings, get_settings
from deal_intel.db.session import get_session_factory
from deal_intel.llm.client import LlmClient
from deal_intel.llm.factory import build_llm_client
from deal_intel.observability.tracing import PostgresTracer, Tracer, utc_now
from deal_intel.orchestration.executor import RunExecutor
from deal_intel.orchestration.runner import Runner, RunnerDeps
from deal_intel.policy.engine import expire_overdue

PYTEST_MODULE = "pytest"


@dataclass
class AppRuntime:
    executor: RunExecutor
    session_factory: sessionmaker[Session]
    settings: Settings
    clock: Callable[[], datetime] = utc_now
    _started: bool = False

    def start(self) -> None:
        if self._started:
            return
        self.executor.start()
        with self.session_factory.begin() as session:
            expire_overdue(session, self.clock())
        self._started = True

    def shutdown(self) -> None:
        if not self._started:
            return
        self.executor.shutdown()
        self._started = False


def build_runtime(
    *,
    settings: Settings | None = None,
    session_factory: sessionmaker[Session] | None = None,
    agents: AgentSuite | None = None,
    llm: LlmClient | None = None,
    tracer: Tracer | None = None,
    clock: Callable[[], datetime] = utc_now,
    inline: bool = False,
) -> AppRuntime:
    resolved_settings = settings or get_settings()
    resolved_factory = session_factory or get_session_factory()
    resolved_tracer = tracer or PostgresTracer(resolved_factory)
    resolved_llm = llm or build_llm_client(resolved_settings, resolved_factory, resolved_tracer)
    runner = Runner(
        RunnerDeps(
            session_factory=resolved_factory,
            llm=resolved_llm,
            tracer=resolved_tracer,
            settings=resolved_settings,
            agents=agents or LIVE_AGENTS,
            clock=clock,
        )
    )
    executor = RunExecutor(runner, resolved_factory, resolved_settings, clock=clock, inline=inline)
    return AppRuntime(
        executor=executor,
        session_factory=resolved_factory,
        settings=resolved_settings,
        clock=clock,
    )


def running_under_pytest() -> bool:
    """Default create_app() must not start the executor against the dev database during tests."""
    return PYTEST_MODULE in sys.modules
