from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from deal_intel.contracts.llm import LlmCallRecord
from deal_intel.db.models import LlmCallRow
from deal_intel.db.writes import column_values


def write_call(session_factory: sessionmaker[Session], record: LlmCallRecord) -> None:
    """Commits on its own, so a call's cost is recorded even if the run later rolls back."""
    with session_factory.begin() as session:
        session.add(LlmCallRow(**column_values(record)))


def spend_since(session: Session, since: datetime) -> Decimal:
    statement = select(func.coalesce(func.sum(LlmCallRow.cost_usd), 0)).where(
        LlmCallRow.created_at >= since
    )
    return Decimal(session.scalar(statement) or 0)
