from sqlalchemy import select
from sqlalchemy.orm import Session

from deal_intel.contracts.reference import UserProfile
from deal_intel.db.models import UserRow


def list_users(session: Session) -> list[UserProfile]:
    """Every user, ordered by id, for the simulated-identity selector. Read-only."""
    rows = session.scalars(select(UserRow).order_by(UserRow.user_id))
    return [UserProfile.model_validate(row, from_attributes=True) for row in rows]
