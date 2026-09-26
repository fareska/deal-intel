"""The in-process run executor (plan change C1): a bounded thread pool, with hooks the API's
lifespan calls in M5.

The queue lives in memory, so a restart loses it. `start` therefore first moves every run left
queued or mid-stage to FAILED/INTERRUPTED, from where it can be resumed.
"""

import logging
from collections.abc import AsyncIterator, Callable
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import asynccontextmanager
from contextvars import copy_context
from datetime import datetime, time
from decimal import Decimal

from sqlalchemy.orm import Session, sessionmaker

from deal_intel.config import Settings
from deal_intel.contracts.runs import RunRecord
from deal_intel.llm.call_log import spend_since
from deal_intel.observability.tracing import utc_now
from deal_intel.orchestration.persistence import mark_interrupted_runs
from deal_intel.orchestration.runner import Runner

logger = logging.getLogger(__name__)

WORKER_THREAD_PREFIX = "run-worker"


class ExecutorNotStarted(RuntimeError):
    pass


class DailyBudgetExceeded(RuntimeError):
    """Today's model spend has reached the budget; new runs wait for tomorrow."""


class RunExecutor:
    def __init__(
        self,
        runner: Runner,
        session_factory: sessionmaker[Session],
        settings: Settings,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._runner = runner
        self._session_factory = session_factory
        self._settings = settings
        self._clock = clock
        self._pool: ThreadPoolExecutor | None = None

    def start(self) -> list[str]:
        """Returns the ids of the runs the sweep marked interrupted."""
        with self._session_factory.begin() as session:
            interrupted = mark_interrupted_runs(session, self._clock())
        self._pool = ThreadPoolExecutor(
            max_workers=self._settings.run_executor_workers,
            thread_name_prefix=WORKER_THREAD_PREFIX,
        )
        if interrupted:
            logger.warning("interrupted runs marked failed", extra={"run_count": len(interrupted)})
        return interrupted

    def submit(self, run_id: str) -> Future[RunRecord]:
        if self._pool is None:
            raise ExecutorNotStarted
        self.require_budget()
        future = self._pool.submit(copy_context().run, self._runner.run, run_id)
        future.add_done_callback(crash_logger(run_id))
        return future

    def require_budget(self) -> None:
        now = self._clock()
        start_of_day = datetime.combine(now.date(), time.min, tzinfo=now.tzinfo)
        with self._session_factory() as session:
            spent = spend_since(session, start_of_day)
        if spent >= Decimal(str(self._settings.daily_cost_budget_usd)):
            raise DailyBudgetExceeded

    def shutdown(self, wait: bool = True) -> None:
        if self._pool is not None:
            self._pool.shutdown(wait=wait)
            self._pool = None


def crash_logger(run_id: str) -> Callable[[Future[RunRecord]], None]:
    """The runner records and logs every stage failure itself; this logs only what escaped it,
    which leaves the run for the startup sweep."""

    def log_crash(future: Future[RunRecord]) -> None:
        if future.cancelled():
            return
        error = future.exception()
        if error is not None:
            logger.error(
                "run crashed outside a stage",
                extra={"run_id": run_id, "error_type": type(error).__name__},
            )

    return log_crash


@asynccontextmanager
async def executor_lifespan(executor: RunExecutor) -> AsyncIterator[RunExecutor]:
    executor.start()
    try:
        yield executor
    finally:
        executor.shutdown()
