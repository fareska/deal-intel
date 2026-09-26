from deal_intel.retrieval.chunkers.base import Chunker
from deal_intel.retrieval.chunkers.gong import chunk_gong_summaries
from deal_intel.retrieval.chunkers.policy import chunk_policy_rules
from deal_intel.retrieval.chunkers.pricing import chunk_pricing_notes
from deal_intel.retrieval.chunkers.salesforce import (
    chunk_accounts,
    chunk_contacts,
    chunk_opportunities,
)
from deal_intel.retrieval.chunkers.slack import chunk_slack_updates
from deal_intel.retrieval.chunkers.transcripts import chunk_transcripts

CHUNKERS: tuple[Chunker, ...] = (
    chunk_opportunities,
    chunk_accounts,
    chunk_contacts,
    chunk_gong_summaries,
    chunk_transcripts,
    chunk_pricing_notes,
    chunk_policy_rules,
    chunk_slack_updates,
)

__all__ = ["CHUNKERS", "Chunker"]
