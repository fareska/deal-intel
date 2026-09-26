from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session

from deal_intel.contracts.base import StrictModel
from deal_intel.contracts.reference import (
    Account,
    Contact,
    Opportunity,
    PricingNote,
    UserProfile,
)
from deal_intel.db.base import Base
from deal_intel.db.models import AccountRow, ContactRow, OpportunityRow, PricingNoteRow, UserRow
from deal_intel.db.writes import column_values, upsert_rows
from deal_intel.retrieval.dataset import DatasetFile
from deal_intel.retrieval.tsv import parse_rows


@dataclass(frozen=True)
class ReferenceSource:
    dataset_file: DatasetFile
    contract: type[StrictModel]
    table: type[Base]


# Foreign keys fix the order: accounts before the rows that refer to them.
REFERENCE_SOURCES: tuple[ReferenceSource, ...] = (
    ReferenceSource(DatasetFile.ACCOUNTS, Account, AccountRow),
    ReferenceSource(DatasetFile.OPPORTUNITIES, Opportunity, OpportunityRow),
    ReferenceSource(DatasetFile.CONTACTS, Contact, ContactRow),
    ReferenceSource(DatasetFile.PRICING_NOTES, PricingNote, PricingNoteRow),
    ReferenceSource(DatasetFile.ACCESS_PERMISSIONS, UserProfile, UserRow),
)


@dataclass(frozen=True)
class ReferenceData:
    """Reference rows parsed from the files and keyed by natural key, for code with no database."""

    users: dict[str, UserProfile]
    accounts: dict[str, Account]
    opportunities: dict[str, Opportunity]
    contacts: dict[str, Contact]
    pricing_notes: dict[str, PricingNote]


def load_reference_data(session: Session, data_root: Path) -> dict[str, int]:
    row_counts: dict[str, int] = {}
    for source in REFERENCE_SOURCES:
        rows = parse_rows(data_root / source.dataset_file, source.contract)
        upsert_rows(session, source.table, [column_values(row) for row in rows])
        row_counts[source.table.__tablename__] = len(rows)
    return row_counts


def read_reference_data(data_root: Path) -> ReferenceData:
    return ReferenceData(
        users={
            user.user_id: user
            for user in parse_rows(data_root / DatasetFile.ACCESS_PERMISSIONS, UserProfile)
        },
        accounts={
            account.account_id: account
            for account in parse_rows(data_root / DatasetFile.ACCOUNTS, Account)
        },
        opportunities={
            opportunity.opportunity_id: opportunity
            for opportunity in parse_rows(data_root / DatasetFile.OPPORTUNITIES, Opportunity)
        },
        contacts={
            contact.contact_id: contact
            for contact in parse_rows(data_root / DatasetFile.CONTACTS, Contact)
        },
        pricing_notes={
            note.pricing_note_id: note
            for note in parse_rows(data_root / DatasetFile.PRICING_NOTES, PricingNote)
        },
    )
