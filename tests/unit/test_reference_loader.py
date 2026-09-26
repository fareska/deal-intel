from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from deal_intel.db.models import AccountRow, OpportunityRow, UserRow
from deal_intel.retrieval.reference import load_reference_data

EXPECTED_ROW_COUNTS = {
    "accounts": 3,
    "opportunities": 3,
    "contacts": 15,
    "pricing_notes": 5,
    "users": 6,
}


def test_loads_every_table(db_session: Session, synthetic_data: Path) -> None:
    assert load_reference_data(db_session, synthetic_data) == EXPECTED_ROW_COUNTS


def test_reload_is_idempotent(db_session: Session, synthetic_data: Path) -> None:
    load_reference_data(db_session, synthetic_data)
    load_reference_data(db_session, synthetic_data)

    assert db_session.scalar(select(func.count()).select_from(UserRow)) == 6
    assert db_session.scalar(select(func.count()).select_from(AccountRow)) == 3


def test_deal_desk_user_row(db_session: Session, synthetic_data: Path) -> None:
    load_reference_data(db_session, synthetic_data)

    user = db_session.get(UserRow, "USR-5005")

    assert user is not None
    assert user.allowed_account_ids == ["ACC-2001", "ACC-2002", "ACC-2003"]
    assert user.can_view_sensitive_pricing
    assert user.can_request_approval
    assert user.can_view_restricted_account


def test_unauthorized_requester_has_two_source_types(
    db_session: Session, synthetic_data: Path
) -> None:
    load_reference_data(db_session, synthetic_data)

    user = db_session.get(UserRow, "USR-5007")

    assert user is not None
    assert user.allowed_source_types == ["salesforce", "gong"]


def test_restricted_opportunity_row(db_session: Session, synthetic_data: Path) -> None:
    load_reference_data(db_session, synthetic_data)

    opportunity = db_session.get(OpportunityRow, "OPP-1003")

    assert opportunity is not None
    assert opportunity.restricted_access
    assert opportunity.acv == Decimal("1879000")
    assert opportunity.close_date == date(2026, 6, 5)


def test_database_rejects_unknown_access_level(db_session: Session) -> None:
    db_session.add(
        AccountRow(
            account_id="ACC-9999",
            account_name="Check Constraint Probe",
            industry="n/a",
            region="n/a",
            country="n/a",
            employee_band="n/a",
            current_products="n/a",
            account_health="n/a",
            strategic_notes="n/a",
            access_level="secret",
        )
    )

    with pytest.raises(IntegrityError, match="ck_accounts_access_level"):
        db_session.flush()
