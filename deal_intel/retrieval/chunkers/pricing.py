from deal_intel.contracts.access import AccessLevel
from deal_intel.contracts.evidence import ChunkKind, EvidenceChunk
from deal_intel.contracts.reference import Account, Opportunity, PricingNote
from deal_intel.permissions.gate import baseline_access_level
from deal_intel.retrieval.chunkers.base import (
    ChunkDraft,
    IngestContext,
    labelled_text,
    make_chunk,
    percent,
)
from deal_intel.retrieval.dataset import DatasetFile
from deal_intel.retrieval.sensitivity import SensitivityRule


def chunk_pricing_notes(context: IngestContext) -> list[EvidenceChunk]:
    reference = context.reference
    chunks: list[EvidenceChunk] = []
    for note in reference.pricing_notes.values():
        opportunity = reference.opportunities[note.opportunity_id]
        account = reference.accounts[opportunity.account_id]
        chunks.append(
            make_chunk(context, pricing_draft(note, opportunity, account, context.sensitivity))
        )
    return chunks


def pricing_draft(
    note: PricingNote, opportunity: Opportunity, account: Account, rule: SensitivityRule
) -> ChunkDraft:
    sensitive = rule.applies(note, opportunity, account)
    baseline = baseline_access_level(opportunity, account)
    text = labelled_text(
        f"Pricing note {note.pricing_note_id} for {note.opportunity_id}",
        [
            ("Current ACV", note.current_acv),
            ("Proposed ACV", note.proposed_acv),
            ("Requested discount", percent(note.requested_discount)),
            ("Renewal uplift", percent(note.renewal_uplift)),
            ("Commercial risk", note.commercial_risk),
            ("Approval status", note.approval_status),
            ("Notes", note.pricing_notes),
        ],
    )
    return ChunkDraft(
        kind=ChunkKind.PRICING,
        source_key=note.pricing_note_id,
        relative_path=DatasetFile.PRICING_NOTES,
        source_id=note.pricing_note_id,
        opportunity_id=note.opportunity_id,
        account_id=opportunity.account_id,
        access_level=AccessLevel.SENSITIVE_PRICING if sensitive else baseline,
        text=text,
        metadata={
            "approval_status": note.approval_status,
            "commercial_risk": note.commercial_risk.value,
            "requested_discount": str(note.requested_discount),
            "renewal_uplift": str(note.renewal_uplift),
            "sensitive": sensitive,
        },
    )
