"""Runs one attempt of a run: stage order, the parallel subagents, state, and failure handling.

A resumed run starts a new attempt and skips every settled stage, so only failed or unstarted
work is redone. A subagent's model failure degrades the run and it carries on; any other stage
failure, a scope violation, or a spent token budget fails it.
"""

import logging
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import AbstractContextManager
from contextvars import copy_context
from dataclasses import dataclass
from datetime import datetime
from functools import partial

from sqlalchemy.orm import Session, sessionmaker

from deal_intel.agents.pipeline import LIVE_AGENTS, AgentSuite
from deal_intel.agents.scope_guard import ScopeViolation
from deal_intel.config import Settings
from deal_intel.contracts.base import StrictModel
from deal_intel.contracts.runs import (
    STAGE_ORDER,
    STAGE_STATES,
    STARTABLE_STATES,
    SUBAGENT_STAGES,
    WORKING_STATES,
    AttemptStarted,
    DenialDetail,
    FailureDetail,
    LeakIncident,
    RunErrorCode,
    RunRecord,
    RunState,
    StageCompleted,
    StageError,
    StageName,
    StageStatus,
)
from deal_intel.contracts.tracing import SpanAttribute, SpanKind
from deal_intel.db.models import RunRow
from deal_intel.db.session import autocommit_factory
from deal_intel.guardrails.render_checks import GuardrailFailure
from deal_intel.llm.client import LlmClient
from deal_intel.llm.errors import LlmError
from deal_intel.observability.tracing import SpanHandle, Tracer, utc_now
from deal_intel.orchestration.persistence import (
    first_unsettled_stage,
    get_run,
    latest_attempt,
    latest_outputs,
    lock_run,
    persist_stage,
    settled_stages,
    stage_output,
    successful_outputs,
    transition,
)
from deal_intel.orchestration.stages import (
    Commit,
    StageEnv,
    authorize_stage,
    deal_snapshot_stage,
    guardrails_stage,
    policy_stage,
    render_stage,
    retrieve_stage,
    strategy_stage,
    subagent_stage,
)

logger = logging.getLogger(__name__)

RUN_SPAN = "run"
STAGE_SPAN_PREFIX = "stage."
# The subagents share a step and run in parallel; every other step is one stage.
STAGE_STEPS: tuple[tuple[StageName, ...], ...] = (
    (StageName.AUTHORIZE,),
    (StageName.RETRIEVE,),
    (StageName.DEAL_SNAPSHOT,),
    SUBAGENT_STAGES,
    (StageName.NEGOTIATION_STRATEGY,),
    (StageName.POLICY,),
    (StageName.GUARDRAILS,),
    (StageName.RENDER,),
)
STAGE_WORK: Mapping[StageName, Callable[[StageEnv], Commit]] = {
    StageName.AUTHORIZE: authorize_stage,
    StageName.RETRIEVE: retrieve_stage,
    StageName.DEAL_SNAPSHOT: deal_snapshot_stage,
    StageName.CONVERSATION_INTELLIGENCE: partial(
        subagent_stage, stage=StageName.CONVERSATION_INTELLIGENCE
    ),
    StageName.STAKEHOLDER_MAP: partial(subagent_stage, stage=StageName.STAKEHOLDER_MAP),
    StageName.NEGOTIATION_STRATEGY: strategy_stage,
    StageName.POLICY: policy_stage,
    StageName.GUARDRAILS: guardrails_stage,
    StageName.RENDER: render_stage,
}


class RunNotStartable(RuntimeError):
    def __init__(self, run_id: str, state: RunState) -> None:
        super().__init__(f"run {run_id} is {state.value}")
        self.state = state


class StageFailed(RuntimeError):
    def __init__(self, stage: StageName | None, error: Exception) -> None:
        super().__init__(stage.value if stage else RUN_SPAN)
        self.stage = stage
        self.error = error


@dataclass(frozen=True)
class RunnerDeps:
    session_factory: sessionmaker[Session]
    llm: LlmClient
    tracer: Tracer
    settings: Settings
    agents: AgentSuite = LIVE_AGENTS
    clock: Callable[[], datetime] = utc_now


@dataclass(frozen=True)
class Attempt:
    run_id: str
    number: int
    settled: frozenset[StageName]


class Runner:
    def __init__(self, deps: RunnerDeps) -> None:
        self._deps = deps
        self._read_factory = autocommit_factory(deps.session_factory)

    def run(self, run_id: str) -> RunRecord:
        attempt = self._start_attempt(run_id)
        with self._deps.tracer.span(RUN_SPAN, SpanKind.RUN, run_attributes(self._record(run_id))):
            try:
                self._run_steps(attempt)
            except StageFailed as failure:
                self._fail(attempt, failure.stage, failure.error)
        return self._record(run_id)

    def _run_steps(self, attempt: Attempt) -> None:
        for step in STAGE_STEPS:
            if self._record(attempt.run_id).state not in WORKING_STATES:
                return
            stages = [stage for stage in step if stage not in attempt.settled]
            if len(stages) > 1:
                self._run_parallel(attempt, stages)
            elif stages:
                self._run_single(attempt, stages[0])

    def _start_attempt(self, run_id: str) -> Attempt:
        now = self._deps.clock()
        with self._deps.session_factory.begin() as session:
            row = lock_run(session, run_id)
            state = RunState(row.state)
            outputs = latest_outputs(session, run_id)
            first = first_unsettled_stage(outputs)
            if state not in STARTABLE_STATES or first is None:
                raise RunNotStartable(run_id, state)
            number = latest_attempt(session, run_id) + 1
            transition(
                session, row, STAGE_STATES[first], number, AttemptStarted(attempt=number), now
            )
        return Attempt(run_id=run_id, number=number, settled=settled_stages(outputs))

    def _record(self, run_id: str) -> RunRecord:
        with self._read_factory() as session:
            return get_run(session, run_id)

    def _env(self, attempt: Attempt) -> StageEnv:
        with self._read_factory() as session:
            run = get_run(session, attempt.run_id)
            outputs = successful_outputs(session, attempt.run_id)
            reused_from = None if run.fresh else run.reused_from_run_id
            reused = successful_outputs(session, reused_from) if reused_from else {}
        deps = self._deps
        return StageEnv(
            run=run,
            attempt=attempt.number,
            outputs=outputs,
            reused_outputs=reused,
            read_factory=self._read_factory,
            llm=deps.llm,
            tracer=deps.tracer,
            settings=deps.settings,
            agents=deps.agents,
            now=deps.clock,
        )

    def _run_single(self, attempt: Attempt, stage: StageName) -> None:
        try:
            env = self._env(attempt)
            with self._stage_span(env, stage) as span:
                detail = self._apply(attempt, stage, STAGE_WORK[stage](env))
                if isinstance(detail, DenialDetail):
                    span.set_attributes({SpanAttribute.REASON_CODE: detail.reason_code})
        except Exception as error:
            raise StageFailed(stage, error) from error

    def _run_parallel(self, attempt: Attempt, stages: Sequence[StageName]) -> None:
        """Each subagent computes on its own thread and session; outputs are then persisted in
        stage order, so the recorded state never moves backwards."""
        try:
            env = self._env(attempt)
        except Exception as error:
            raise StageFailed(None, error) from error
        with ThreadPoolExecutor(max_workers=len(stages)) as pool:
            futures = [
                pool.submit(copy_context().run, self._compute_traced, env, stage)
                for stage in stages
            ]
        fatal: StageFailed | None = None
        for stage, future in zip(stages, futures, strict=True):
            error = future.exception()
            try:
                if error is None:
                    self._apply(attempt, stage, future.result())
                elif isinstance(error, LlmError):
                    self._apply_degraded(attempt, stage, error)
                elif isinstance(error, Exception):
                    raise error
            except Exception as failure:
                fatal = fatal or StageFailed(stage, failure)
        if fatal is not None:
            raise fatal

    def _compute_traced(self, env: StageEnv, stage: StageName) -> Commit:
        with self._stage_span(env, stage):
            return STAGE_WORK[stage](env)

    def _stage_span(self, env: StageEnv, stage: StageName) -> AbstractContextManager[SpanHandle]:
        attributes = {SpanAttribute.RUN_ID: env.run.run_id, SpanAttribute.STAGE: stage}
        return self._deps.tracer.span(STAGE_SPAN_PREFIX + stage.value, SpanKind.STAGE, attributes)

    def _apply(self, attempt: Attempt, stage: StageName, commit: Commit) -> StrictModel:
        """The stage's writes, output row, and event in one short transaction."""
        now = self._deps.clock()
        with self._deps.session_factory.begin() as session:
            row = lock_run(session, attempt.run_id)
            result = commit(session, row)
            persist_stage(session, row, result.output)
            to_state = result.to_state or following_state(stage)
            detail = result.detail or StageCompleted(
                stage=stage, attempt=attempt.number, status=StageStatus.SUCCEEDED
            )
            if to_state is not RunState.DENIED and self._over_budget(row):
                to_state = RunState.FAILED
                detail = FailureDetail(error_code=RunErrorCode.BUDGET_EXCEEDED, stage=stage)
            transition(session, row, to_state, attempt.number, detail, now)
        if isinstance(detail, FailureDetail):
            log_failure(attempt.run_id, stage, detail.error_code, None)
        return detail

    def _apply_degraded(self, attempt: Attempt, stage: StageName, error: LlmError) -> None:
        now = self._deps.clock()
        output = stage_output(
            attempt.run_id,
            stage,
            attempt.number,
            StageError(error_code=error.error_code),
            status=StageStatus.FAILED,
        )
        detail = StageCompleted(stage=stage, attempt=attempt.number, status=StageStatus.FAILED)
        with self._deps.session_factory.begin() as session:
            row = lock_run(session, attempt.run_id)
            persist_stage(session, row, output)
            row.degraded = True
            transition(session, row, following_state(stage), attempt.number, detail, now)
        logger.warning(
            "subagent failed; the run continues degraded",
            extra={
                "run_id": attempt.run_id,
                "stage": stage.value,
                "error_code": error.error_code,
                "error_type": type(error).__name__,
            },
        )

    def _fail(self, attempt: Attempt, stage: StageName | None, error: Exception) -> None:
        now = self._deps.clock()
        code = failure_code(error)
        with self._deps.session_factory.begin() as session:
            row = lock_run(session, attempt.run_id)
            if stage is not None:
                failed = stage_output(
                    attempt.run_id,
                    stage,
                    attempt.number,
                    StageError(error_code=code),
                    status=StageStatus.FAILED,
                )
                persist_stage(session, row, failed)
            for incident in leak_incidents(error):
                transition(session, row, RunState(row.state), attempt.number, incident, now)
            transition(
                session,
                row,
                RunState.FAILED,
                attempt.number,
                FailureDetail(error_code=code, stage=stage),
                now,
            )
        log_failure(attempt.run_id, stage, code, error)

    def _over_budget(self, row: RunRow) -> bool:
        return row.input_tokens > self._deps.settings.run_input_token_budget


def following_state(stage: StageName) -> RunState:
    index = STAGE_ORDER.index(stage) + 1
    return STAGE_STATES[STAGE_ORDER[index]] if index < len(STAGE_ORDER) else RunState.COMPLETED


def run_attributes(run: RunRecord) -> dict[SpanAttribute, object]:
    return {
        SpanAttribute.RUN_ID: run.run_id,
        SpanAttribute.OPPORTUNITY_ID: run.opportunity_id,
        SpanAttribute.USER_ID: run.user_id,
    }


def failure_code(error: Exception) -> str:
    if isinstance(error, ScopeViolation):
        return RunErrorCode.SCOPE_VIOLATION
    if isinstance(error, LlmError | GuardrailFailure):
        return error.error_code
    return RunErrorCode.UNEXPECTED_ERROR


def leak_incidents(error: Exception) -> list[LeakIncident]:
    if not isinstance(error, GuardrailFailure):
        return []
    return [LeakIncident(kind=hit.kind, canary_sha256=hit.canary_sha256) for hit in error.hits]


def log_failure(
    run_id: str, stage: StageName | None, error_code: str, error: Exception | None
) -> None:
    """The one log line per failed run. No message text: it may quote evidence."""
    level = logging.ERROR if error_code == RunErrorCode.SCOPE_VIOLATION else logging.WARNING
    logger.log(
        level,
        "run failed",
        extra={
            "run_id": run_id,
            "stage": stage.value if stage else None,
            "error_code": error_code,
            "error_type": type(error).__name__ if error else None,
        },
    )
