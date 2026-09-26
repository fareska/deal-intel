from enum import StrEnum
from pathlib import Path

CITATION_ROOT = "synthetic_data"
DEFAULT_DATA_ROOT = Path(CITATION_ROOT)
TRANSCRIPTS_DIR = "gong/transcripts"
TRANSCRIPT_GLOB = "*.md"


class DatasetFile(StrEnum):
    """Paths relative to the dataset root."""

    ACCOUNTS = "salesforce/accounts.tsv"
    OPPORTUNITIES = "salesforce/opportunities.tsv"
    CONTACTS = "salesforce/contacts.tsv"
    GONG_SUMMARIES = "gong/gong_call_summaries.tsv"
    PRICING_NOTES = "pricing/pricing_notes.tsv"
    ACCESS_PERMISSIONS = "policies/access_permissions.tsv"
    DEAL_DESK_POLICY = "policies/deal_desk_policy.md"
    SLACK_UPDATES = "slack/account_team_updates.tsv"


EVIDENCE_FILES: tuple[DatasetFile, ...] = (
    DatasetFile.ACCOUNTS,
    DatasetFile.OPPORTUNITIES,
    DatasetFile.CONTACTS,
    DatasetFile.GONG_SUMMARIES,
    DatasetFile.PRICING_NOTES,
    DatasetFile.DEAL_DESK_POLICY,
    DatasetFile.SLACK_UPDATES,
)
OPTIONAL_EVIDENCE_FILES: frozenset[DatasetFile] = frozenset({DatasetFile.SLACK_UPDATES})


def citation_path(relative_path: str) -> str:
    """Citations name files relative to the repository, whatever `--path` the CLI received."""
    return f"{CITATION_ROOT}/{relative_path}"


def relative_to_root(data_root: Path, path: Path) -> str:
    return path.relative_to(data_root).as_posix()


def transcript_paths(data_root: Path) -> list[Path]:
    return sorted((data_root / TRANSCRIPTS_DIR).glob(TRANSCRIPT_GLOB))
