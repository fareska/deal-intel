from sqlalchemy import select
from sqlalchemy.orm import Session

from deal_intel.contracts.api import OpportunityChoice
from deal_intel.db.models import OpportunityRow


def list_opportunities(session: Session) -> list[OpportunityChoice]:
    """Every opportunity, ordered by id, for the new-run selector. Read-only."""
    rows = session.scalars(select(OpportunityRow).order_by(OpportunityRow.opportunity_id))
    return [
        OpportunityChoice(opportunity_id=row.opportunity_id, opportunity_name=row.opportunity_name)
        for row in rows
    ]
