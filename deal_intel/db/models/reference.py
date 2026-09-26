from datetime import date
from decimal import Decimal

from sqlalchemy import CheckConstraint, ForeignKey, Numeric, Text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from deal_intel.contracts.reference import ACCOUNT_ACCESS_LEVELS, LowMediumHigh
from deal_intel.db.base import Base
from deal_intel.db.models.checks import values_check

MONEY = Numeric(14, 2)
PERCENT = Numeric(6, 2)


class AccountRow(Base):
    __tablename__ = "accounts"
    __table_args__ = (
        values_check("access_level", ACCOUNT_ACCESS_LEVELS, "ck_accounts_access_level"),
    )

    account_id: Mapped[str] = mapped_column(primary_key=True)
    account_name: Mapped[str]
    industry: Mapped[str]
    region: Mapped[str]
    country: Mapped[str]
    employee_band: Mapped[str]
    current_products: Mapped[str]
    account_health: Mapped[str]
    strategic_notes: Mapped[str]
    access_level: Mapped[str]


class OpportunityRow(Base):
    __tablename__ = "opportunities"
    __table_args__ = (
        CheckConstraint("probability BETWEEN 0 AND 100", name="ck_opportunities_probability"),
        CheckConstraint("renewal_term_months > 0", name="ck_opportunities_renewal_term_months"),
        values_check("risk_level", LowMediumHigh, "ck_opportunities_risk_level"),
    )

    opportunity_id: Mapped[str] = mapped_column(primary_key=True)
    opportunity_name: Mapped[str]
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.account_id"))
    account_name: Mapped[str]
    stage: Mapped[str]
    type: Mapped[str]
    region: Mapped[str]
    country: Mapped[str]
    industry: Mapped[str]
    owner: Mapped[str]
    close_date: Mapped[date]
    acv: Mapped[Decimal] = mapped_column(MONEY)
    tcv: Mapped[Decimal] = mapped_column(MONEY)
    renewal_term_months: Mapped[int]
    probability: Mapped[int]
    forecast_category: Mapped[str]
    next_step: Mapped[str]
    primary_competitor: Mapped[str]
    risk_level: Mapped[str]
    approval_required: Mapped[bool]
    restricted_access: Mapped[bool]


class ContactRow(Base):
    __tablename__ = "contacts"
    __table_args__ = (
        values_check("influence_level", LowMediumHigh, "ck_contacts_influence_level"),
    )

    contact_id: Mapped[str] = mapped_column(primary_key=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.account_id"))
    full_name: Mapped[str]
    title: Mapped[str]
    role_in_deal: Mapped[str]
    email: Mapped[str]
    phone: Mapped[str]
    location: Mapped[str]
    influence_level: Mapped[str]
    sentiment: Mapped[str]
    last_interaction_date: Mapped[date]
    notes: Mapped[str]


class PricingNoteRow(Base):
    __tablename__ = "pricing_notes"
    __table_args__ = (
        values_check("commercial_risk", LowMediumHigh, "ck_pricing_notes_commercial_risk"),
    )

    pricing_note_id: Mapped[str] = mapped_column(primary_key=True)
    opportunity_id: Mapped[str] = mapped_column(ForeignKey("opportunities.opportunity_id"))
    current_acv: Mapped[Decimal] = mapped_column(MONEY)
    proposed_acv: Mapped[Decimal] = mapped_column(MONEY)
    requested_discount: Mapped[Decimal] = mapped_column(PERCENT)
    renewal_uplift: Mapped[Decimal] = mapped_column(PERCENT)
    commercial_risk: Mapped[str]
    approval_status: Mapped[str]
    pricing_notes: Mapped[str]


class UserRow(Base):
    __tablename__ = "users"

    user_id: Mapped[str] = mapped_column(primary_key=True)
    user_name: Mapped[str]
    role: Mapped[str]
    allowed_account_ids: Mapped[list[str]] = mapped_column(ARRAY(Text))
    allowed_source_types: Mapped[list[str]] = mapped_column(ARRAY(Text))
    can_view_sensitive_pricing: Mapped[bool]
    can_request_approval: Mapped[bool]
    can_view_restricted_account: Mapped[bool]
