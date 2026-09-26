"""The only code that touches `runs`, `run_events`, and `stage_outputs`.

Every function works in the caller's transaction; the runner wraps each stage's writes in one
short transaction so the output, the event, and the state change commit together. Events are
only ever added, never updated.
"""

import uuid
from collections.abc import Mapping
from datetime import datetime

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from deal_intel.contracts.base import StrictModel
from deal_intel.contracts.runs import (
    INTERRUPTIBLE_STATES,
    STAGE_ORDER,
    SUBAGENT_STAGES,
    WORKING_STATES,
    FailureDetail,
    RunErrorCode,
    RunEvent,
    RunRecord,
    RunRequest,
    RunState,
    StageName,
    StageOutput,
    StageStatus,
)
from deal_intel.db.models import RunEventRow, RunRow, StageOutputRow
from deal_intel.db.writes import column_values

COMPLETION_STATES: frozenset[RunState] = frozenset({RunState.COMPLETED, RunState.DENIED})
REUSABLE_STATES: tuple[RunState, ...] = (RunState.COMPLETED, RunState.AWAITING_APPROVAL)
IN_FLIGHT_STATES: frozenset[RunState] = WORKING_STATES | {RunState.QUEUED}
IDEMPOTENT_STATES: frozenset[RunState] = (
    frozenset(REUSABLE_STATES) | IN_FLIGHT_STATES | {RunState.FAILED}
)
NO_ATTEMPT = 0


class RunNotFound(LookupError):
    pass


def create_run(session: Session, request: RunRequest, now: datetime) -> RunRecord:
    row = RunRow(
        run_id=uuid.uuid4().hex,
        opportunity_id=request.opportunity_id,
        user_id=request.user_id,
        fresh=request.fresh,
        state=RunState.QUEUED.value,
        degraded=False,
        input_tokens=0,
        cost_usd=0,
        created_at=now,
        updated_at=now,
    )
    session.add(row)
    session.flush()
    return run_record(row)


def run_record(row: RunRow) -> RunRecord:
    return RunRecord.model_validate(row, from_attributes=True)


def get_run(session: Session, run_id: str) -> RunRecord:
    row = session.get(RunRow, run_id)
    if row is None:
        raise RunNotFound(run_id)
    return run_record(row)


def lock_run(session: Session, run_id: str) -> RunRow:
    row = session.get(RunRow, run_id, with_for_update=True)
    if row is None:
        raise RunNotFound(run_id)
    return row


def transition(
    session: Session,
    row: RunRow,
    to_state: RunState,
    attempt: int,
    detail: StrictModel,
    now: datetime,
) -> None:
    """Appends the event and moves the run; `to_state` may equal the current state, which
    records a stage completing inside one state."""
    session.add(
        RunEventRow(
            run_id=row.run_id,
            attempt=attempt,
            from_state=row.state,
            to_state=to_state.value,
            detail=detail.model_dump(mode="json"),
            at=now,
        )
    )
    row.state = to_state.value
    row.updated_at = now
    row.completed_at = now if to_state in COMPLETION_STATES else None


def run_events(session: Session, run_id: str) -> list[RunEvent]:
    statement = (
        select(RunEventRow).where(RunEventRow.run_id == run_id).order_by(RunEventRow.event_id)
    )
    return [
        RunEvent.model_validate(row, from_attributes=True) for row in session.scalars(statement)
    ]


def persist_stage(session: Session, row: RunRow, output: StageOutput) -> None:
    session.add(StageOutputRow(**column_values(output)))
    row.input_tokens += output.input_tokens
    row.cost_usd += output.cost_usd


def stage_output(
    run_id: str,
    stage: StageName,
    attempt: int,
    output: BaseModel,
    status: StageStatus = StageStatus.SUCCEEDED,
    **metadata: object,
) -> StageOutput:
    return StageOutput(
        run_id=run_id,
        stage=stage,
        attempt=attempt,
        status=status,
        output_json=output.model_dump(mode="json"),
        **metadata,
    )


def latest_outputs(session: Session, run_id: str) -> dict[StageName, StageOutput]:
    """The latest attempt's row per stage, succeeded or failed."""
    statement = (
        select(StageOutputRow)
        .where(StageOutputRow.run_id == run_id)
        .order_by(StageOutputRow.attempt)
    )
    latest: dict[StageName, StageOutput] = {}
    for row in session.scalars(statement):
        latest[StageName(row.stage)] = StageOutput.model_validate(row, from_attributes=True)
    return latest


def successful_outputs(session: Session, run_id: str) -> dict[StageName, StageOutput]:
    """The latest succeeded attempt per stage."""
    statement = (
        select(StageOutputRow)
        .where(
            StageOutputRow.run_id == run_id,
            StageOutputRow.status == StageStatus.SUCCEEDED.value,
        )
        .order_by(StageOutputRow.attempt)
    )
    outputs: dict[StageName, StageOutput] = {}
    for row in session.scalars(statement):
        outputs[StageName(row.stage)] = StageOutput.model_validate(row, from_attributes=True)
    return outputs


def settled_stages(outputs: Mapping[StageName, StageOutput]) -> frozenset[StageName]:
    """Stages a resumed attempt skips. A failed subagent counts as settled once the strategy
    has succeeded, because the strategy's output already reflects that input as missing."""
    settled = {stage for stage, output in outputs.items() if output.status is StageStatus.SUCCEEDED}
    if StageName.NEGOTIATION_STRATEGY in settled:
        settled.update(stage for stage in SUBAGENT_STAGES if stage in outputs)
    return frozenset(settled)


def first_unsettled_stage(outputs: Mapping[StageName, StageOutput]) -> StageName | None:
    settled = settled_stages(outputs)
    return next((stage for stage in STAGE_ORDER if stage not in settled), None)


def latest_attempt(session: Session, run_id: str) -> int:
    statement = select(func.max(RunEventRow.attempt)).where(RunEventRow.run_id == run_id)
    return session.scalar(statement) or NO_ATTEMPT


def find_run_for_key(session: Session, idempotency_key: str) -> RunRecord | None:
    """The newest non-degraded run that already used this key, including failed and in-flight."""
    statement = (
        select(RunRow)
        .where(
            RunRow.idempotency_key == idempotency_key,
            RunRow.state.in_([state.value for state in IDEMPOTENT_STATES]),
            RunRow.degraded.is_(False),
        )
        .order_by(RunRow.created_at.desc(), RunRow.run_id)
        .limit(1)
    )
    row = session.scalar(statement)
    return None if row is None else run_record(row)


def find_in_flight_run(session: Session, opportunity_id: str, user_id: str) -> RunRecord | None:
    """A queued or working run for this pair that has not stored its key yet."""
    statement = (
        select(RunRow)
        .where(
            RunRow.opportunity_id == opportunity_id,
            RunRow.user_id == user_id,
            RunRow.fresh.is_(False),
            RunRow.state.in_([state.value for state in IN_FLIGHT_STATES]),
        )
        .order_by(RunRow.created_at.desc(), RunRow.run_id)
        .limit(1)
    )
    row = session.scalar(statement)
    return None if row is None else run_record(row)


def find_reusable_run(session: Session, idempotency_key: str, run_id: str) -> str | None:
    """A degraded run is never reused: its outputs are missing an input a new run may have."""
    statement = (
        select(RunRow.run_id)
        .where(
            RunRow.idempotency_key == idempotency_key,
            RunRow.run_id != run_id,
            RunRow.state.in_([state.value for state in REUSABLE_STATES]),
            RunRow.degraded.is_(False),
        )
        .order_by(RunRow.created_at.desc(), RunRow.run_id)
        .limit(1)
    )
    return session.scalar(statement)


def mark_interrupted_runs(session: Session, now: datetime) -> list[str]:
    """Fails every run left in a working state by a stopped process, so it can be resumed."""
    statement = (
        select(RunRow)
        .where(RunRow.state.in_([state.value for state in INTERRUPTIBLE_STATES]))
        .order_by(RunRow.run_id)
        .with_for_update(skip_locked=True)
    )
    interrupted = list(session.scalars(statement))
    for row in interrupted:
        attempt = latest_attempt(session, row.run_id)
        detail = FailureDetail(error_code=RunErrorCode.INTERRUPTED)
        transition(session, row, RunState.FAILED, attempt, detail, now)
    return [row.run_id for row in interrupted]
