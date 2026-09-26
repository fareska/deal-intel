from collections.abc import Mapping, Sequence

from deal_intel.contracts.evidence import ChunkKind, EvidenceChunk
from deal_intel.contracts.reference import Contact, GongCallSummary
from deal_intel.retrieval.chunkers.base import ChunkDraft, IngestContext, labelled_text, make_chunk
from deal_intel.retrieval.dataset import DatasetFile
from deal_intel.retrieval.tsv import parse_rows

# Each participant reads "Name, Title", so participants need a separator other than a comma.
PARTICIPANT_SEPARATOR = "; "
SUMMARY_LABEL = "Summary"


def chunk_gong_summaries(context: IngestContext) -> list[EvidenceChunk]:
    summaries = parse_rows(context.data_root / DatasetFile.GONG_SUMMARIES, GongCallSummary)
    return [
        make_chunk(context, summary_draft(summary, context.reference.contacts))
        for summary in summaries
    ]


def resolve_participants(contact_ids: Sequence[str], contacts: Mapping[str, Contact]) -> list[str]:
    """Unknown ids are kept as written rather than dropped."""
    return [
        f"{contacts[contact_id].full_name}, {contacts[contact_id].title}"
        if contact_id in contacts
        else contact_id
        for contact_id in contact_ids
    ]


def summary_draft(summary: GongCallSummary, contacts: Mapping[str, Contact]) -> ChunkDraft:
    participants = PARTICIPANT_SEPARATOR.join(resolve_participants(summary.participants, contacts))
    text = labelled_text(
        f"Gong call {summary.call_id}: {summary.title}",
        [
            ("Opportunity", summary.opportunity_id),
            ("Call date", summary.call_date),
            ("Stage at call", summary.stage_at_call),
            ("Duration", summary.duration),
            ("Participants", participants),
            (SUMMARY_LABEL, summary.summary),
            ("Key points", summary.key_points),
            ("Customer sentiment", summary.customer_sentiment),
            ("Risks", summary.risks),
            ("Next steps", summary.next_steps),
        ],
    )
    return ChunkDraft(
        kind=ChunkKind.GONG_SUMMARY,
        source_key=summary.call_id,
        relative_path=DatasetFile.GONG_SUMMARIES,
        source_id=summary.call_id,
        opportunity_id=summary.opportunity_id,
        account_id=summary.account_id,
        access_level=summary.source_access_level,
        text=text,
        metadata={
            "title": summary.title,
            "stage_at_call": summary.stage_at_call,
            "customer_sentiment": summary.customer_sentiment,
            "participants": list(summary.participants),
        },
        event_date=summary.call_date,
        author_or_speakers=participants,
    )
