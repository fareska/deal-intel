"""The runner over hand-authored agent outputs: stage order, states, degradation, failure,
resume, budgets, reuse, and the render-time guardrails that can fail a run."""

import hashlib
import json
import logging
import threading

import pytest
from sqlalchemy import func, select

from deal_intel.agents.scope_guard import ScopeViolation
from deal_intel.contracts.access import DenialReason
from deal_intel.contracts.agents.common import AgentName
from deal_intel.contracts.agents.negotiation_strategy import SummarySentence
from deal_intel.contracts.approvals import ApproverRole, RuleId
from deal_intel.contracts.guardrails import Confidence
from deal_intel.contracts.runs import (
    RunErrorCode,
    RunState,
    StageName,
    StageStatus,
)
from deal_intel.contracts.tracing import SpanKind
from deal_intel.db.models import ApprovalRow, LlmCallRow, StageOutputRow, TraceSpanRow
from deal_intel.guardrails.canaries import CanaryKind, denial_leaks
from deal_intel.llm.errors import LlmErrorCode, ModelRefusal
from deal_intel.orchestration.runner import RunNotStartable
from deal_intel.rendering.sections import AGENT_TITLES, DEGRADED_WARNING

NS = AgentName.NEGOTIATION_STRATEGY
CI = AgentName.CONVERSATION_INTELLIGENCE
SM = AgentName.STAKEHOLDER_MAP
LLM_AGENTS = (CI, SM, NS)
HIDDEN_SLACK_ID = "SLK-1001-01"


def strategy_with(agents, **update) -> None:
    agents.outputs[NS] = agents.outputs[NS].model_copy(update=update)


def asserting_summary() -> list[SummarySentence]:
    return [
        SummarySentence(
            text=f"Item {index} has been approved.",
            evidence_ids=["sfdc_opp:OPP-1001"],
            confidence=Confidence.HIGH,
        )
        for index in ("one", "two", "three")
    ]


def test_allowed_run_completes_through_every_stage(run_bench) -> None:
    agents = run_bench.agents("OPP-1001")

    record = run_bench.run(agents, "USR-5001", "OPP-1001")

    assert record.state is RunState.COMPLETED
    assert not record.degraded
    stages = {row.stage: row.status for row in run_bench.stage_rows(record.run_id)}
    assert stages == dict.fromkeys(StageName, StageStatus.SUCCEEDED)
    assert [event.detail.get("stage") for event in run_bench.events(record.run_id)] == [
        None,
        *[stage.value for stage in StageName],
    ]
    assert record.input_tokens == len(LLM_AGENTS) * agents.usage.input_tokens
    assert record.evidence_hash is not None
    assert record.idempotency_key is not None
    assert [brief.version for brief in run_bench.briefs(record.run_id)] == [1]


def test_subagents_run_in_parallel_on_separate_sessions(run_bench) -> None:
    agents = run_bench.agents("OPP-1001")
    agents.subagent_barrier = threading.Barrier(2)

    record = run_bench.run(agents, "USR-5001", "OPP-1001")

    assert record.state is RunState.COMPLETED
    assert agents.threads[CI] != agents.threads[SM]
    assert threading.get_ident() not in (agents.threads[CI], agents.threads[SM])
    assert agents.runtimes[CI].retriever is not agents.runtimes[SM].retriever


def test_denied_run_stops_after_authorize_and_records_only_the_reason(run_bench) -> None:
    agents = run_bench.agents("OPP-1003")

    record = run_bench.run(agents, "USR-5007", "OPP-1003")

    assert record.state is RunState.DENIED
    assert sum(agents.calls.values()) == 0
    assert [row.stage for row in run_bench.stage_rows(record.run_id)] == [StageName.AUTHORIZE]
    assert [event.detail for event in run_bench.events(record.run_id)] == [
        {"attempt": 1},
        {"reason_code": DenialReason.ACCOUNT_NOT_ALLOWED.value},
    ]
    assert (record.snapshot_id, record.evidence_hash) == (None, None)
    with run_bench.session_factory() as session:
        retrieval_spans = select(func.count()).where(TraceSpanRow.kind == SpanKind.RETRIEVAL)
        assert session.scalar(retrieval_spans) == 0
        assert session.scalar(select(func.count()).select_from(LlmCallRow)) == 0


UNKNOWN_OPPORTUNITY = "OPP-9999"


def span_shape(spans) -> list[tuple[str, str, str, tuple[str, ...]]]:
    """What a reader can compare across two traces once ids and timings are set aside."""
    return sorted(
        (span.kind, span.name, span.status, tuple(sorted(span.attributes or {})))
        for span in spans
    )


def test_denied_traces_look_identical_for_unknown_and_forbidden_opportunities(run_bench) -> None:
    agents = run_bench.agents("OPP-1003")
    unknown = run_bench.run(agents, "USR-5007", UNKNOWN_OPPORTUNITY)
    forbidden = run_bench.run(agents, "USR-5007", "OPP-1003")

    assert unknown.state is forbidden.state is RunState.DENIED
    assert span_shape(run_bench.spans(unknown.run_id)) == span_shape(
        run_bench.spans(forbidden.run_id)
    )


def test_no_span_of_a_denied_run_names_the_reason(run_bench) -> None:
    record = run_bench.run(run_bench.agents("OPP-1003"), "USR-5007", "OPP-1003")

    assert all(
        "reason_code" not in (span.attributes or {}) for span in run_bench.spans(record.run_id)
    )


def test_denied_run_leaks_nothing_through_events_or_spans(run_bench) -> None:
    record = run_bench.run(run_bench.agents("OPP-1003"), "USR-5007", "OPP-1003")
    payload = json.dumps({"reason_code": DenialReason.ACCOUNT_NOT_ALLOWED.value})

    with run_bench.session_factory() as session:
        hits = denial_leaks(session, record.run_id, ["USR-5007", "OPP-1003"], [payload])

    assert hits == []


def test_subagent_model_failure_degrades_the_run_and_it_completes(run_bench, caplog) -> None:
    """USR-5007 may not request approvals, so nothing holds the degraded brief back."""
    agents = run_bench.agents("OPP-1001")
    agents.failures[CI] = ModelRefusal("refused")

    with caplog.at_level(logging.WARNING):
        record = run_bench.run(agents, "USR-5007", "OPP-1001")

    assert (record.state, record.degraded) == (RunState.COMPLETED, True)
    stages = {row.stage: row.status for row in run_bench.stage_rows(record.run_id)}
    assert stages[StageName.CONVERSATION_INTELLIGENCE] is StageStatus.FAILED
    assert stages[StageName.RENDER] is StageStatus.SUCCEEDED
    brief = run_bench.briefs(record.run_id)[-1]
    assert brief.json["metadata"]["degraded"] is True
    assert brief.json["confidence"]["degraded_components"] == [CI.value]
    degraded_warning = DEGRADED_WARNING.format(agent=AGENT_TITLES[CI])
    assert degraded_warning in brief.json["confidence"]["warnings"]
    assert degraded_warning in brief.markdown
    assert not any(brief.json["buyer_goals"].values())
    assert [entry.message for entry in caplog.records if entry.levelno == logging.WARNING] == [
        "subagent failed; the run continues degraded"
    ]


def test_degraded_run_routes_its_actions_to_human_review(run_bench) -> None:
    """Missing source data fires rule R7 (plan change C13), so a reader who may request
    approvals gets a human-review approval per action before the run completes."""
    agents = run_bench.agents("OPP-1001")
    agents.failures[CI] = ModelRefusal("refused")

    record = run_bench.run(agents, "USR-5001", "OPP-1001")

    assert (record.state, record.degraded) == (RunState.AWAITING_APPROVAL, True)
    with run_bench.session_factory() as session:
        approvals = list(session.scalars(select(ApprovalRow).order_by(ApprovalRow.subject_id)))
    assert [(row.subject_id, row.required_role, row.rule_ids) for row in approvals] == [
        ("action:A1", ApproverRole.HUMAN_REVIEWER.value, [RuleId.R7.value]),
        ("action:A2", ApproverRole.HUMAN_REVIEWER.value, [RuleId.R7.value]),
    ]


def test_strategy_failure_fails_the_run(run_bench) -> None:
    agents = run_bench.agents("OPP-1001")
    agents.failures[NS] = ModelRefusal("refused")

    record = run_bench.run(agents, "USR-5001", "OPP-1001")

    assert record.state is RunState.FAILED
    assert run_bench.events(record.run_id)[-1].detail == {
        "error_code": LlmErrorCode.MODEL_REFUSAL.value,
        "stage": StageName.NEGOTIATION_STRATEGY.value,
    }
    assert run_bench.briefs(record.run_id) == []


def test_resume_reruns_only_the_failed_stage_in_a_new_attempt(run_bench) -> None:
    agents = run_bench.agents("OPP-1001")
    agents.failures[NS] = ModelRefusal("refused")
    failed = run_bench.run(agents, "USR-5001", "OPP-1001")
    del agents.failures[NS]

    resumed = run_bench.runner(agents).run(failed.run_id)

    assert resumed.state is RunState.COMPLETED
    assert agents.calls == {AgentName.DEAL_SNAPSHOT: 1, CI: 1, SM: 1, NS: 2}
    attempts = {row.stage: row.attempt for row in run_bench.stage_rows(failed.run_id)}
    assert attempts[StageName.CONVERSATION_INTELLIGENCE] == 1
    assert attempts[StageName.NEGOTIATION_STRATEGY] == 2
    assert attempts[StageName.RENDER] == 2
    events = run_bench.events(failed.run_id)
    resumed_events = [event for event in events if event.attempt == 2]
    assert resumed_events[0].detail == {"attempt": 2}
    assert resumed_events[0].to_state is RunState.SYNTHESIZING


def test_finished_run_cannot_be_started_again(run_bench) -> None:
    agents = run_bench.agents("OPP-1001")
    record = run_bench.run(agents, "USR-5001", "OPP-1001")

    with pytest.raises(RunNotStartable):
        run_bench.runner(agents).run(record.run_id)


def test_scope_violation_fails_the_run_rather_than_degrading_it(run_bench, caplog) -> None:
    agents = run_bench.agents("OPP-1001")
    agents.failures[SM] = ScopeViolation("cited a chunk outside scope", ["slack:SLK-1001-01"])

    with caplog.at_level(logging.WARNING):
        record = run_bench.run(agents, "USR-5007", "OPP-1001")

    assert (record.state, record.degraded) == (RunState.FAILED, False)
    assert run_bench.events(record.run_id)[-1].detail == {
        "error_code": RunErrorCode.SCOPE_VIOLATION.value,
        "stage": StageName.STAKEHOLDER_MAP.value,
    }
    assert agents.calls[NS] == 0
    [failure] = [entry for entry in caplog.records if entry.message == "run failed"]
    assert failure.levelno == logging.ERROR
    assert HIDDEN_SLACK_ID not in caplog.text


def test_run_over_its_token_budget_fails(run_bench) -> None:
    agents = run_bench.agents("OPP-1001")
    budget = agents.usage.input_tokens + agents.usage.input_tokens // 2
    settings = run_bench.settings.model_copy(update={"run_input_token_budget": budget})

    record = run_bench.run(agents, "USR-5001", "OPP-1001", settings=settings)

    assert record.state is RunState.FAILED
    assert run_bench.events(record.run_id)[-1].detail["error_code"] == (
        RunErrorCode.BUDGET_EXCEEDED.value
    )
    assert agents.calls[NS] == 0


def test_repeat_run_reuses_agent_outputs_at_no_cost(run_bench) -> None:
    first = run_bench.run(run_bench.agents("OPP-1001"), "USR-5001", "OPP-1001")
    agents = run_bench.agents("OPP-1001")

    second = run_bench.run(agents, "USR-5001", "OPP-1001")

    assert second.state is RunState.COMPLETED
    assert second.reused_from_run_id == first.run_id
    assert second.idempotency_key == first.idempotency_key
    assert all(agents.calls[agent] == 0 for agent in LLM_AGENTS)
    assert (second.input_tokens, second.cost_usd) == (0, 0)
    assert run_bench.briefs(second.run_id)[0].markdown == run_bench.briefs(first.run_id)[0].markdown


def test_fresh_run_skips_the_reuse_lookup(run_bench) -> None:
    first = run_bench.run(run_bench.agents("OPP-1001"), "USR-5001", "OPP-1001")
    agents = run_bench.agents("OPP-1001")

    second = run_bench.run(agents, "USR-5001", "OPP-1001", fresh=True)

    assert second.reused_from_run_id is None
    assert second.idempotency_key == first.idempotency_key
    assert all(agents.calls[agent] == 1 for agent in LLM_AGENTS)


def test_degraded_run_is_never_reused(run_bench) -> None:
    degraded = run_bench.agents("OPP-1001")
    degraded.failures[SM] = ModelRefusal("refused")
    run_bench.run(degraded, "USR-5001", "OPP-1001")

    second = run_bench.run(run_bench.agents("OPP-1001"), "USR-5001", "OPP-1001")

    assert second.reused_from_run_id is None


def test_summary_that_asserts_approvals_fails_the_run(run_bench) -> None:
    agents = run_bench.agents("OPP-1001")
    strategy_with(agents, executive_summary=asserting_summary())

    record = run_bench.run(agents, "USR-5001", "OPP-1001")

    assert record.state is RunState.FAILED
    assert run_bench.events(record.run_id)[-1].detail == {
        "error_code": RunErrorCode.APPROVAL_ASSERTION.value,
        "stage": StageName.GUARDRAILS.value,
    }


def test_action_that_asserts_an_approval_is_withheld_and_the_run_completes(run_bench) -> None:
    agents = run_bench.agents("OPP-1001")
    [first, second] = agents.outputs[NS].next_actions
    asserting = first.model_copy(update={"action": "Send the schedule, which was approved."})
    strategy_with(agents, next_actions=[asserting, second])

    record = run_bench.run(agents, "USR-5001", "OPP-1001")

    assert record.state is RunState.COMPLETED
    brief = run_bench.briefs(record.run_id)[-1]
    assert [action["id"] for action in brief.json["next_actions"]["actions"]] == ["A2"]
    assert "was approved" not in brief.markdown


def test_leaked_hidden_content_fails_the_run_and_stores_only_its_hash(run_bench) -> None:
    agents = run_bench.agents("OPP-1001")
    [first, second] = agents.outputs[NS].next_actions
    leaking = first.model_copy(update={"action": f"Follow up on {HIDDEN_SLACK_ID} internally."})
    strategy_with(agents, next_actions=[leaking, second])

    record = run_bench.run(agents, "USR-5007", "OPP-1001")

    assert record.state is RunState.FAILED
    events = run_bench.events(record.run_id)
    incidents = [event.detail for event in events if "canary_sha256" in event.detail]
    assert incidents == [
        {
            "incident": RunErrorCode.LEAKAGE_DETECTED.value,
            "kind": CanaryKind.ID.value,
            "canary_sha256": hashlib.sha256(HIDDEN_SLACK_ID.encode()).hexdigest(),
        }
    ]
    assert events[-1].detail["error_code"] == RunErrorCode.LEAKAGE_DETECTED.value
    assert all(HIDDEN_SLACK_ID not in json.dumps(event.detail) for event in events)
    assert run_bench.briefs(record.run_id) == []
    with run_bench.session_factory() as session:
        render_rows = select(StageOutputRow).where(
            StageOutputRow.run_id == record.run_id, StageOutputRow.stage == StageName.RENDER
        )
        [render] = session.scalars(render_rows)
        assert HIDDEN_SLACK_ID not in json.dumps(render.output_json)
