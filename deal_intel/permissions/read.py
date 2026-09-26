"""Read-time access to a run: the requester, or a reader whose own scope covers the brief."""

from sqlalchemy.orm import Session

from deal_intel.contracts.access import AccessLevel, Allowed
from deal_intel.contracts.runs import AuthorizeOutput, RunRecord, StageName
from deal_intel.orchestration.outputs import optional_output
from deal_intel.orchestration.persistence import successful_outputs
from deal_intel.permissions.gate import authorize
from deal_intel.rendering.brief import latest_brief


def can_read_run(session: Session, reader_user_id: str, run: RunRecord) -> bool:
    if reader_user_id == run.user_id:
        return True
    decision = authorize(session, reader_user_id, run.opportunity_id)
    if not isinstance(decision, Allowed):
        return False
    return decision.scope.max_access_level >= brief_level_of(session, run)


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
