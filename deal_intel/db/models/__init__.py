"""Imports every table module so `Base.metadata` is complete for Alembic and test truncation."""

from deal_intel.db.models.evidence import EvidenceChunkRow, IngestSnapshotRow
from deal_intel.db.models.llm import AgentOutputCacheRow, LlmCallRow
from deal_intel.db.models.reference import (
    AccountRow,
    ContactRow,
    OpportunityRow,
    PricingNoteRow,
    UserRow,
)
from deal_intel.db.models.tracing import TraceSpanRow

__all__ = [
    "AccountRow",
    "AgentOutputCacheRow",
    "ContactRow",
    "EvidenceChunkRow",
    "IngestSnapshotRow",
    "LlmCallRow",
    "OpportunityRow",
    "PricingNoteRow",
    "TraceSpanRow",
    "UserRow",
]
