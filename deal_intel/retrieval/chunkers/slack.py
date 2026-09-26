import logging

from deal_intel.contracts.evidence import ChunkKind, EvidenceChunk
from deal_intel.contracts.reference import SlackUpdate
from deal_intel.retrieval.chunkers.base import ChunkDraft, IngestContext, make_chunk
from deal_intel.retrieval.dataset import DatasetFile
from deal_intel.retrieval.tsv import parse_rows

logger = logging.getLogger(__name__)


def chunk_slack_updates(context: IngestContext) -> list[EvidenceChunk]:
    path = context.data_root / DatasetFile.SLACK_UPDATES
    if not path.exists():
        logger.warning("%s is missing; run `deal-intel generate-slack` to create it", path)
        return []
    return [make_chunk(context, slack_draft(update)) for update in parse_rows(path, SlackUpdate)]


def slack_draft(update: SlackUpdate) -> ChunkDraft:
    heading = (
        f"Slack update {update.update_id} in {update.channel} "
        f"on {update.update_date.isoformat()} by {update.author_role}"
    )
    return ChunkDraft(
        kind=ChunkKind.SLACK,
        source_key=update.update_id,
        relative_path=DatasetFile.SLACK_UPDATES,
        source_id=update.update_id,
        opportunity_id=update.opportunity_id,
        account_id=update.account_id,
        access_level=update.source_access_level,
        text=f"{heading}\n{update.update_text}",
        metadata={
            "channel": update.channel,
            "author_role": update.author_role.value,
            "synthetic_notice": update.synthetic_notice,
        },
        event_date=update.update_date,
        author_or_speakers=update.author_role.value,
    )
