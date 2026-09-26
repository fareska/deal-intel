"""Read-time access to a run: the requester, or a second reader whose own scope admits every
chunk the brief was built from."""

from sqlalchemy.orm import Session

from deal_intel.contracts.access import AccessLevel, AccessScope, Allowed
from deal_intel.contracts.runs import AuthorizeOutput, RunRecord, StageName
from deal_intel.orchestration.outputs import optional_output, stored_analysis, stored_retrieval
from deal_intel.orchestration.persistence import successful_outputs
from deal_intel.permissions.gate import authorize
from deal_intel.rendering.brief import latest_brief
from deal_intel.rendering.evidence import cited_chunk_ids
from deal_intel.retrieval.retriever import ScopedRetriever


def can_read_run(session: Session, reader_user_id: str, run: RunRecord) -> bool:
    if reader_user_id == run.user_id:
        return True
    decision = authorize(session, reader_user_id, run.opportunity_id)
    if not isinstance(decision, Allowed):
        return False
    return scope_admits_every_cited_chunk(session, decision.scope, run)


def scope_admits_every_cited_chunk(session: Session, scope: AccessScope, run: RunRecord) -> bool:
    """A run without a brief has nothing a second reader may see yet, so it stays hidden."""
    if latest_brief(session, run.run_id) is None:
        return False
    outputs = successful_outputs(session, run.run_id)
    cited = set(cited_chunk_ids(stored_analysis(outputs)))
    if not cited:
        return True
    retriever = ScopedRetriever(session, scope, snapshot_id=stored_retrieval(outputs).snapshot_id)
    return len(retriever.get(cited).chunks) == len(cited)


def reader_level(session: Session, reader_user_id: str, run: RunRecord) -> AccessLevel | None:
    """The reader's clearance on this opportunity, or None when they have no scope."""
    if reader_user_id == run.user_id:
        stored = stored_run_level(session, run)
        decision = authorize(session, reader_user_id, run.opportunity_id)
        if isinstance(decision, Allowed):
            return decision.scope.max_access_level
        return stored
    decision = authorize(session, reader_user_id, run.opportunity_id)
    return decision.scope.max_access_level if isinstance(decision, Allowed) else None


def brief_level_of(session: Session, run: RunRecord) -> AccessLevel:
    row = latest_brief(session, run.run_id)
    if row is not None:
        return AccessLevel(row.max_access_level)
    return stored_run_level(session, run)


def stored_run_level(session: Session, run: RunRecord) -> AccessLevel:
    """The run's own scope while no brief exists; standard when the run was denied."""
    outputs = successful_outputs(session, run.run_id)
    authorized = optional_output(outputs, StageName.AUTHORIZE, AuthorizeOutput)
    if authorized is not None and authorized.scope is not None:
        return authorized.scope.max_access_level
    return AccessLevel.STANDARD
