"""Run, brief, trace, replay, and resume services. UI and HTTP routes call these."""

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from deal_intel.api.errors import Conflict, Forbidden, NotFound
from deal_intel.config import Settings
from deal_intel.contracts.access import Allowed
from deal_intel.contracts.api import (
    AgentUsage,
    BriefFormat,
    BriefResponse,
    BriefVersionSummary,
    CreateRunRequest,
    RunAccepted,
    RunStatusResponse,
    TraceResponse,
    TraceSpan,
    denial_message,
)
from deal_intel.contracts.brief import Brief, BriefSource
from deal_intel.contracts.llm import LlmCallRecord
from deal_intel.contracts.runs import (
    REPLAYABLE_STATES,
    STARTABLE_STATES,
    RunRecord,
    RunRequest,
    RunState,
    StageName,
)
from deal_intel.contracts.tracing import SpanAttribute
from deal_intel.db.models import BriefRow, LlmCallRow, TraceSpanRow
from deal_intel.observability.tracing import utc_now
from deal_intel.orchestration.executor import DailyBudgetExceeded, RunExecutor
from deal_intel.orchestration.persistence import (
    RunNotFound,
    create_run,
    find_in_flight_run,
    find_run_for_key,
    get_run,
    latest_outputs,
)
from deal_intel.orchestration.stages import idempotency_key_for
from deal_intel.permissions.gate import authorize
from deal_intel.permissions.read import brief_level_of, can_read_run, reader_level
from deal_intel.policy.store import pending_approval_count
from deal_intel.rendering.brief import (
    ReplayUnavailable,
    latest_brief,
    list_brief_rows,
    list_brief_versions,
    replay,
)
from deal_intel.retrieval.retriever import ScopedRetriever

SAFE_TRACE_ATTRIBUTE_KEYS = frozenset(
    {
        SpanAttribute.RUN_ID.value,
        SpanAttribute.STAGE.value,
        SpanAttribute.AGENT_NAME.value,
        SpanAttribute.MODEL.value,
        SpanAttribute.INPUT_TOKENS.value,
        SpanAttribute.OUTPUT_TOKENS.value,
        SpanAttribute.CACHE_READ_TOKENS.value,
        SpanAttribute.CACHE_WRITE_TOKENS.value,
        SpanAttribute.COST_USD.value,
        SpanAttribute.LATENCY_MS.value,
    }
)
USAGE_STAGES: tuple[StageName, ...] = (
    StageName.CONVERSATION_INTELLIGENCE,
    StageName.STAKEHOLDER_MAP,
    StageName.NEGOTIATION_STRATEGY,
)


def start_run(
    session: Session,
    request: CreateRunRequest,
    executor: RunExecutor,
    settings: Settings,
    now: datetime | None = None,
) -> RunAccepted:
    stamp = now or utc_now()
    existing = None if request.fresh else lookup_existing_run(session, request, settings)
    if existing is not None:
        return accept_existing(session, existing, executor)
    record = create_run(session, as_run_request(request), stamp)
    session.commit()
    submit_run(executor, record.run_id)
    return RunAccepted(run_id=record.run_id, state=record.state, existing=False)


def lookup_existing_run(
    session: Session, request: CreateRunRequest, settings: Settings
) -> RunRecord | None:
    in_flight = find_in_flight_run(session, request.opportunity_id, request.user_id)
    if in_flight is not None:
        return in_flight
    decision = authorize(session, request.user_id, request.opportunity_id)
    if not isinstance(decision, Allowed):
        return None
    evidence = ScopedRetriever(session, decision.scope).evidence_hash()
    key = idempotency_key_for(request.opportunity_id, request.user_id, evidence.value, settings)
    return find_run_for_key(session, key)


def accept_existing(session: Session, existing: RunRecord, executor: RunExecutor) -> RunAccepted:
    if existing.state is RunState.FAILED:
        session.commit()
        submit_run(executor, existing.run_id)
        return RunAccepted(run_id=existing.run_id, state=RunState.QUEUED, existing=True)
    return RunAccepted(run_id=existing.run_id, state=existing.state, existing=True)


def submit_run(executor: RunExecutor, run_id: str) -> None:
    try:
        executor.submit(run_id)
    except DailyBudgetExceeded as error:
        raise Conflict() from error


def as_run_request(request: CreateRunRequest) -> RunRequest:
    return RunRequest(
        opportunity_id=request.opportunity_id, user_id=request.user_id, fresh=request.fresh
    )


def load_readable_run(session: Session, run_id: str, reader_user_id: str) -> RunRecord:
    try:
        run = get_run(session, run_id)
    except RunNotFound as error:
        raise NotFound() from error
    if not can_read_run(session, reader_user_id, run):
        raise NotFound()
    return run


def load_owned_run(session: Session, run_id: str, reader_user_id: str) -> RunRecord:
    """Replay and resume write new state and spend the requester's budget, so only they may."""
    run = load_readable_run(session, run_id, reader_user_id)
    if reader_user_id != run.user_id:
        raise Forbidden()
    return run


def run_status(session: Session, run_id: str, reader_user_id: str) -> RunStatusResponse:
    run = load_readable_run(session, run_id, reader_user_id)
    return status_response(session, run)


def status_response(session: Session, run: RunRecord) -> RunStatusResponse:
    return RunStatusResponse(
        run_id=run.run_id,
        opportunity_id=run.opportunity_id,
        user_id=run.user_id,
        state=run.state,
        degraded=run.degraded,
        fresh=run.fresh,
        created_at=run.created_at,
        updated_at=run.updated_at,
        completed_at=run.completed_at,
        duration_ms=duration_ms(run),
        cost_usd=run.cost_usd,
        input_tokens=run.input_tokens,
        usage_by_agent=usage_by_agent(session, run.run_id),
        pending_approvals=pending_approval_count(session, run.run_id),
        message=denial_message() if run.state is RunState.DENIED else None,
    )


def duration_ms(run: RunRecord) -> int | None:
    end = run.completed_at or (run.updated_at if run.state is not RunState.QUEUED else None)
    if end is None:
        return None
    return int((end - run.created_at).total_seconds() * 1000)


def usage_by_agent(session: Session, run_id: str) -> list[AgentUsage]:
    from_calls = usage_from_calls(session, run_id)
    return from_calls or usage_from_stages(session, run_id)


def usage_from_calls(session: Session, run_id: str) -> list[AgentUsage]:
    statement = (
        select(
            LlmCallRow.agent_name,
            func.coalesce(func.sum(LlmCallRow.input_tokens), 0),
            func.coalesce(func.sum(LlmCallRow.output_tokens), 0),
            func.coalesce(func.sum(LlmCallRow.cache_read_input_tokens), 0),
            func.coalesce(func.sum(LlmCallRow.cost_usd), 0),
        )
        .where(LlmCallRow.run_id == run_id)
        .group_by(LlmCallRow.agent_name)
        .order_by(LlmCallRow.agent_name)
    )
    return [
        AgentUsage(
            agent_name=agent_name,
            input_tokens=int(input_tokens),
            output_tokens=int(output_tokens),
            cache_read_tokens=int(cache_read_tokens),
            cost_usd=cost_usd,
        )
        for agent_name, input_tokens, output_tokens, cache_read_tokens, cost_usd in session.execute(
            statement
        )
    ]


def usage_from_stages(session: Session, run_id: str) -> list[AgentUsage]:
    outputs = latest_outputs(session, run_id)
    return [
        AgentUsage(
            agent_name=stage.value,
            input_tokens=output.input_tokens,
            output_tokens=output.output_tokens,
            cache_read_tokens=0,
            cost_usd=output.cost_usd,
        )
        for stage in USAGE_STAGES
        if (output := outputs.get(stage)) is not None
    ]


def latest_brief_response(
    session: Session, run_id: str, reader_user_id: str, fmt: BriefFormat
) -> BriefResponse:
    run = load_readable_run(session, run_id, reader_user_id)
    row = latest_brief(session, run.run_id)
    if row is None:
        raise NotFound()
    return brief_response(row, fmt)


def brief_versions(session: Session, run_id: str, reader_user_id: str) -> list[BriefVersionSummary]:
    run = load_readable_run(session, run_id, reader_user_id)
    return [
        BriefVersionSummary(version=item.version, source=item.source, rendered_at=item.rendered_at)
        for item in list_brief_versions(session, run.run_id)
    ]


def readable_brief_rows(session: Session, run_id: str, reader_user_id: str) -> list[BriefRow]:
    run = load_readable_run(session, run_id, reader_user_id)
    return list_brief_rows(session, run.run_id)


def llm_calls_for_reader(session: Session, run_id: str, reader_user_id: str) -> list[LlmCallRecord]:
    run = load_readable_run(session, run_id, reader_user_id)
    statement = (
        select(LlmCallRow)
        .where(LlmCallRow.run_id == run.run_id)
        .order_by(LlmCallRow.created_at, LlmCallRow.call_id)
    )
    return [
        LlmCallRecord.model_validate(row, from_attributes=True)
        for row in session.scalars(statement)
    ]


def brief_response(row: BriefRow, fmt: BriefFormat) -> BriefResponse:
    return BriefResponse(
        run_id=row.run_id,
        version=row.version,
        source=BriefSource(row.source),
        markdown=row.markdown if fmt is BriefFormat.MARKDOWN else None,
        brief=Brief.model_validate(row.json) if fmt is BriefFormat.JSON else None,
    )


def run_trace(session: Session, run_id: str, reader_user_id: str) -> TraceResponse:
    run = load_readable_run(session, run_id, reader_user_id)
    redacted = should_redact_trace(session, reader_user_id, run)
    statement = (
        select(TraceSpanRow)
        .where(TraceSpanRow.run_id == run.run_id)
        .order_by(TraceSpanRow.started_at, TraceSpanRow.span_id)
    )
    spans = [to_trace_span(row, redacted=redacted) for row in session.scalars(statement)]
    return TraceResponse(run_id=run.run_id, redacted=redacted, spans=spans)


def should_redact_trace(session: Session, reader_user_id: str, run: RunRecord) -> bool:
    level = reader_level(session, reader_user_id, run)
    if level is None:
        return True
    return level < brief_level_of(session, run)


def to_trace_span(row: TraceSpanRow, *, redacted: bool) -> TraceSpan:
    attributes = row.attributes or {}
    if redacted:
        attributes = {
            key: value for key, value in attributes.items() if key in SAFE_TRACE_ATTRIBUTE_KEYS
        }
    return TraceSpan(
        span_id=row.span_id,
        parent_span_id=row.parent_span_id,
        kind=row.kind,
        name=row.name,
        started_at=row.started_at,
        ended_at=row.ended_at,
        status=row.status,
        duration_ms=span_duration_ms(row),
        attributes=attributes,
    )


def span_duration_ms(row: TraceSpanRow) -> int | None:
    if row.ended_at is None:
        return None
    return int((row.ended_at - row.started_at).total_seconds() * 1000)


def replay_run(
    session: Session, run_id: str, reader_user_id: str, now: datetime | None = None
) -> BriefResponse:
    run = load_owned_run(session, run_id, reader_user_id)
    if run.state not in REPLAYABLE_STATES:
        raise Conflict()
    try:
        rendered = replay(session, run.run_id, now or utc_now())
    except ReplayUnavailable as error:
        raise Conflict() from error
    session.commit()
    return BriefResponse(
        run_id=run.run_id,
        version=rendered.brief.metadata.version,
        source=rendered.brief.metadata.source,
        markdown=rendered.markdown,
        brief=rendered.brief,
    )


def resume_run(
    session: Session, run_id: str, reader_user_id: str, executor: RunExecutor
) -> RunStatusResponse:
    run = load_owned_run(session, run_id, reader_user_id)
    if run.state not in STARTABLE_STATES:
        raise Conflict()
    session.commit()
    submit_run(executor, run.run_id)
    return run_status(session, run.run_id, reader_user_id)
