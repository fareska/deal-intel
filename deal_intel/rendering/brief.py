"""Renders, scans, and stores brief versions. Every render reads stored outputs only, so a
replay or an approval update never calls a model and yields the same body for the same state."""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from deal_intel.contracts.access import AccessLevel
from deal_intel.contracts.brief import Brief, BriefMetadata, BriefSource, BriefVersion
from deal_intel.contracts.guardrails import GuardrailCheck, GuardrailResult
from deal_intel.contracts.runs import RunErrorCode, RunRecord
from deal_intel.db.models import BriefRow
from deal_intel.guardrails.canaries import build_canary_set, surface_leaks
from deal_intel.guardrails.render_checks import GuardrailFailure
from deal_intel.guardrails.validators import passed
from deal_intel.orchestration.outputs import (
    stored_analysis,
    stored_guardrails,
    stored_policy,
    stored_retrieval,
    stored_scope,
)
from deal_intel.orchestration.persistence import get_run, lock_run, successful_outputs
from deal_intel.policy.store import approval_views
from deal_intel.rendering.evidence import brief_access_level, cited_chunk_ids, load_cited_chunks
from deal_intel.rendering.markdown import to_markdown
from deal_intel.rendering.sections import (
    BriefInputs,
    buyer_goals_section,
    citer_for,
    confidence_section,
    executive_summary_section,
    missing_information_section,
    negotiation_state_section,
    next_actions_section,
    snapshot_section,
    source_evidence_section,
    stakeholder_section,
)
from deal_intel.retrieval.retriever import ScopedRetriever

NO_VERSION = 0
FIRST_VERSION = 1


class ReplayUnavailable(LookupError):
    """The run has no rendered brief, so there is nothing to replay."""


@dataclass(frozen=True)
class RenderedBrief:
    brief: Brief
    markdown: str
    results: list[GuardrailResult]

    def version(self) -> BriefVersion:
        metadata = self.brief.metadata
        return BriefVersion(
            run_id=metadata.run_id,
            version=metadata.version,
            source=metadata.source,
            max_access_level=metadata.max_access_level,
            rendered_at=metadata.rendered_at,
        )


def load_brief_inputs(session: Session, run_id: str) -> BriefInputs:
    outputs = successful_outputs(session, run_id)
    scope = stored_scope(outputs)
    retrieval = stored_retrieval(outputs)
    analysis = stored_analysis(outputs)
    policy = stored_policy(outputs)
    retriever = ScopedRetriever(session, scope, snapshot_id=retrieval.snapshot_id)
    return BriefInputs(
        scope=scope,
        retrieval=retrieval,
        analysis=analysis,
        policy=policy,
        guardrails=stored_guardrails(outputs),
        approvals=approval_views(session, policy.approval_ids),
        chunks=load_cited_chunks(retriever, cited_chunk_ids(analysis)),
    )


@dataclass(frozen=True)
class BriefStamp:
    """What identifies one render; none of it reaches the Markdown body."""

    run: RunRecord
    source: BriefSource
    version: int
    rendered_at: datetime


def build_brief(inputs: BriefInputs, stamp: BriefStamp) -> tuple[Brief, list[GuardrailResult]]:
    citer = citer_for(inputs)
    analysis = inputs.analysis
    strategy = analysis.strategy.output
    findings = analysis.findings.output if analysis.findings else None
    stakeholders = analysis.stakeholders.output if analysis.stakeholders else None
    snapshot = snapshot_section(analysis.snapshot, citer)
    summary = executive_summary_section(strategy, citer)
    buyer_goals = buyer_goals_section(findings, citer)
    stakeholder_map = stakeholder_section(stakeholders, citer)
    negotiation_state = negotiation_state_section(strategy, findings, citer)
    actions = next_actions_section(inputs, citer)
    confidence = confidence_section(inputs, citer, actions.results)
    evidence = source_evidence_section(
        [
            snapshot,
            summary,
            buyer_goals,
            stakeholder_map,
            negotiation_state,
            actions.section,
            confidence,
        ],
        inputs.chunks,
    )
    brief = Brief(
        metadata=brief_metadata(
            stamp, brief_access_level(inputs.chunks[entry.chunk_id] for entry in evidence.entries)
        ),
        deal_snapshot=snapshot,
        executive_summary=summary,
        buyer_goals=buyer_goals,
        stakeholder_map=stakeholder_map,
        negotiation_state=negotiation_state,
        next_actions=actions.section,
        missing_information=missing_information_section(inputs),
        source_evidence=evidence,
        confidence=confidence,
    )
    return brief, actions.results


def brief_metadata(stamp: BriefStamp, max_access_level: AccessLevel) -> BriefMetadata:
    return BriefMetadata(
        run_id=stamp.run.run_id,
        opportunity_id=stamp.run.opportunity_id,
        version=stamp.version,
        source=stamp.source,
        max_access_level=max_access_level,
        degraded=stamp.run.degraded,
        cost_usd=stamp.run.cost_usd,
        rendered_at=stamp.rendered_at,
    )


def render_brief(
    session: Session, run_id: str, source: BriefSource, version: int, now: datetime
) -> RenderedBrief:
    run = get_run(session, run_id)
    inputs = load_brief_inputs(session, run_id)
    brief, results = build_brief(inputs, BriefStamp(run, source, version, now))
    markdown = to_markdown(brief)
    canaries = build_canary_set(
        session, inputs.retrieval.snapshot_id, inputs.scope, request_texts(run)
    )
    hits = surface_leaks(session, run_id, [markdown, brief.model_dump_json()], canaries)
    if hits:
        raise GuardrailFailure(RunErrorCode.LEAKAGE_DETECTED, hits)
    return RenderedBrief(
        brief=brief,
        markdown=markdown,
        results=[*results, *passed(GuardrailCheck.LEAKAGE_CANARIES)],
    )


def request_texts(run: RunRecord) -> list[str]:
    """What the reader typed; echoing it back reveals nothing."""
    return [run.user_id, run.opportunity_id]


def next_version(session: Session, run_id: str) -> int:
    latest = session.scalar(select(func.max(BriefRow.version)).where(BriefRow.run_id == run_id))
    return (latest or NO_VERSION) + 1


def store_brief(session: Session, rendered: RenderedBrief) -> None:
    metadata = rendered.brief.metadata
    session.add(
        BriefRow(
            run_id=metadata.run_id,
            version=metadata.version,
            source=metadata.source.value,
            markdown=rendered.markdown,
            json=rendered.brief.model_dump(mode="json"),
            max_access_level=metadata.max_access_level.value,
            guardrail_results=[result.model_dump(mode="json") for result in rendered.results],
            rendered_at=metadata.rendered_at,
        )
    )


def render_and_store(session: Session, run_id: str, source: BriefSource, now: datetime) -> int:
    version = next_version(session, run_id)
    store_brief(session, render_brief(session, run_id, source, version, now))
    return version


def replay(session: Session, run_id: str, now: datetime) -> RenderedBrief:
    """Re-renders from stored outputs into a new version. The run lock orders it against
    approval decisions, so the version number and the approval state it reflects agree."""
    lock_run(session, run_id)
    version = next_version(session, run_id)
    if version == FIRST_VERSION:
        raise ReplayUnavailable(run_id)
    rendered = render_brief(session, run_id, BriefSource.REPLAY, version, now)
    store_brief(session, rendered)
    return rendered


def latest_brief(session: Session, run_id: str) -> BriefRow | None:
    statement = (
        select(BriefRow).where(BriefRow.run_id == run_id).order_by(BriefRow.version.desc()).limit(1)
    )
    return session.scalar(statement)


def list_brief_rows(session: Session, run_id: str) -> list[BriefRow]:
    statement = select(BriefRow).where(BriefRow.run_id == run_id).order_by(BriefRow.version)
    return list(session.scalars(statement))


def list_brief_versions(session: Session, run_id: str) -> list[BriefVersion]:
    return [
        BriefVersion(
            run_id=row.run_id,
            version=row.version,
            source=BriefSource(row.source),
            max_access_level=AccessLevel(row.max_access_level),
            rendered_at=row.rendered_at,
        )
        for row in list_brief_rows(session, run_id)
    ]
