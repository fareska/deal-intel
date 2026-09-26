"""Authorisation lookups: one reference row read by primary key and returned as a contract."""

from sqlalchemy.orm import Session

from deal_intel.contracts.base import StrictModel
from deal_intel.contracts.reference import Account, Opportunity, UserProfile
from deal_intel.db.base import Base
from deal_intel.db.models import AccountRow, OpportunityRow, UserRow


def find_user(session: Session, user_id: str) -> UserProfile | None:
    return find_contract(session, UserRow, UserProfile, user_id)


def find_opportunity(session: Session, opportunity_id: str) -> Opportunity | None:
    return find_contract(session, OpportunityRow, Opportunity, opportunity_id)


def find_account(session: Session, account_id: str) -> Account | None:
    return find_contract(session, AccountRow, Account, account_id)


def find_contract[ContractT: StrictModel](
    session: Session, table: type[Base], contract: type[ContractT], key: str
) -> ContractT | None:
    row = session.get(table, key)
    return None if row is None else contract.model_validate(row, from_attributes=True)
