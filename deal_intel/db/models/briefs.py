from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from deal_intel.contracts.access import AccessLevel
from deal_intel.contracts.brief import BriefSource
from deal_intel.db.base import Base
from deal_intel.db.models.checks import values_check


class BriefRow(Base):
    __tablename__ = "briefs"
    __table_args__ = (
        values_check("source", BriefSource, "ck_briefs_source"),
        values_check("max_access_level", AccessLevel, "ck_briefs_max_access_level"),
    )

    run_id: Mapped[str] = mapped_column(ForeignKey("runs.run_id"), primary_key=True)
    version: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str]
    markdown: Mapped[str]
    json: Mapped[dict[str, Any]] = mapped_column(JSONB)
    max_access_level: Mapped[str]
    guardrail_results: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    rendered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
