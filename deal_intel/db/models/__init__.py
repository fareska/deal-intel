"""Imports every table module so `Base.metadata` is complete for Alembic and test truncation."""

from deal_intel.db.models.evidence import EvidenceChunkRow, IngestSnapshotRow
from deal_intel.db.models.reference import (
    AccountRow,
    ContactRow,
    OpportunityRow,
    PricingNoteRow,
    UserRow,
)

__all__ = [
    "AccountRow",
    "ContactRow",
    "EvidenceChunkRow",
    "IngestSnapshotRow",
    "OpportunityRow",
    "PricingNoteRow",
    "UserRow",
]
