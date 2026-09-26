"""Imports every table module so `Base.metadata` is complete for Alembic and test truncation."""

from deal_intel.db.models.approvals import ApprovalEventRow, ApprovalRow
from deal_intel.db.models.briefs import BriefRow
from deal_intel.db.models.evidence import EvidenceChunkRow, IngestSnapshotRow
from deal_intel.db.models.llm import AgentOutputCacheRow, LlmCallRow
from deal_intel.db.models.reference import (
    AccountRow,
    ContactRow,
    OpportunityRow,
    PricingNoteRow,
    UserRow,
)
from deal_intel.db.models.runs import RunEventRow, RunRow, StageOutputRow
from deal_intel.db.models.tracing import TraceSpanRow

__all__ = [
    "AccountRow",
    "AgentOutputCacheRow",
    "ApprovalEventRow",
    "ApprovalRow",
    "BriefRow",
    "ContactRow",
    "EvidenceChunkRow",
    "IngestSnapshotRow",
    "LlmCallRow",
    "OpportunityRow",
    "PricingNoteRow",
    "RunEventRow",
    "RunRow",
    "StageOutputRow",
    "TraceSpanRow",
    "UserRow",
]
