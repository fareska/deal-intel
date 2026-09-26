from sqlalchemy import select
from sqlalchemy.orm import Session

from deal_intel.contracts.access import Allowed
from deal_intel.contracts.api import OpportunityChoice
from deal_intel.db.models import OpportunityRow
from deal_intel.permissions.gate import authorize


def list_opportunities(session: Session) -> list[OpportunityChoice]:
    """Every opportunity, ordered by id. Read-only; never render this to a viewer unfiltered."""
    rows = session.scalars(select(OpportunityRow).order_by(OpportunityRow.opportunity_id))
    return [
        OpportunityChoice(opportunity_id=row.opportunity_id, opportunity_name=row.opportunity_name)
        for row in rows
    ]


def opportunities_open_to(session: Session, viewer_id: str | None) -> list[OpportunityChoice]:
    """Only the deals the viewer may run, so the selector never names another account's deal."""
    if viewer_id is None:
        return []
    return [
        choice
        for choice in list_opportunities(session)
        if isinstance(authorize(session, viewer_id, choice.opportunity_id), Allowed)
    ]
