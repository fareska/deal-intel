"""reference tables

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "accounts",
        sa.Column("account_id", sa.Text(), nullable=False),
        sa.Column("account_name", sa.Text(), nullable=False),
        sa.Column("industry", sa.Text(), nullable=False),
        sa.Column("region", sa.Text(), nullable=False),
        sa.Column("country", sa.Text(), nullable=False),
        sa.Column("employee_band", sa.Text(), nullable=False),
        sa.Column("current_products", sa.Text(), nullable=False),
        sa.Column("account_health", sa.Text(), nullable=False),
        sa.Column("strategic_notes", sa.Text(), nullable=False),
        sa.Column("access_level", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "access_level IN ('standard', 'restricted')", name="ck_accounts_access_level"
        ),
        sa.PrimaryKeyConstraint("account_id"),
    )
    op.create_table(
        "users",
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("user_name", sa.Text(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("allowed_account_ids", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("allowed_source_types", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("can_view_sensitive_pricing", sa.Boolean(), nullable=False),
        sa.Column("can_request_approval", sa.Boolean(), nullable=False),
        sa.Column("can_view_restricted_account", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("user_id"),
    )
    op.create_table(
        "opportunities",
        sa.Column("opportunity_id", sa.Text(), nullable=False),
        sa.Column("opportunity_name", sa.Text(), nullable=False),
        sa.Column("account_id", sa.Text(), nullable=False),
        sa.Column("account_name", sa.Text(), nullable=False),
        sa.Column("stage", sa.Text(), nullable=False),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("region", sa.Text(), nullable=False),
        sa.Column("country", sa.Text(), nullable=False),
        sa.Column("industry", sa.Text(), nullable=False),
        sa.Column("owner", sa.Text(), nullable=False),
        sa.Column("close_date", sa.Date(), nullable=False),
        sa.Column("acv", sa.Numeric(14, 2), nullable=False),
        sa.Column("tcv", sa.Numeric(14, 2), nullable=False),
        sa.Column("renewal_term_months", sa.Integer(), nullable=False),
        sa.Column("probability", sa.Integer(), nullable=False),
        sa.Column("forecast_category", sa.Text(), nullable=False),
        sa.Column("next_step", sa.Text(), nullable=False),
        sa.Column("primary_competitor", sa.Text(), nullable=False),
        sa.Column("risk_level", sa.Text(), nullable=False),
        sa.Column("approval_required", sa.Boolean(), nullable=False),
        sa.Column("restricted_access", sa.Boolean(), nullable=False),
        sa.CheckConstraint("probability BETWEEN 0 AND 100", name="ck_opportunities_probability"),
        sa.CheckConstraint("renewal_term_months > 0", name="ck_opportunities_renewal_term_months"),
        sa.CheckConstraint(
            "risk_level IN ('low', 'medium', 'high')", name="ck_opportunities_risk_level"
        ),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.account_id"]),
        sa.PrimaryKeyConstraint("opportunity_id"),
    )
    op.create_table(
        "contacts",
        sa.Column("contact_id", sa.Text(), nullable=False),
        sa.Column("account_id", sa.Text(), nullable=False),
        sa.Column("full_name", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("role_in_deal", sa.Text(), nullable=False),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("phone", sa.Text(), nullable=False),
        sa.Column("location", sa.Text(), nullable=False),
        sa.Column("influence_level", sa.Text(), nullable=False),
        sa.Column("sentiment", sa.Text(), nullable=False),
        sa.Column("last_interaction_date", sa.Date(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "influence_level IN ('low', 'medium', 'high')", name="ck_contacts_influence_level"
        ),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.account_id"]),
        sa.PrimaryKeyConstraint("contact_id"),
    )
    op.create_table(
        "pricing_notes",
        sa.Column("pricing_note_id", sa.Text(), nullable=False),
        sa.Column("opportunity_id", sa.Text(), nullable=False),
        sa.Column("current_acv", sa.Numeric(14, 2), nullable=False),
        sa.Column("proposed_acv", sa.Numeric(14, 2), nullable=False),
        sa.Column("requested_discount", sa.Numeric(6, 2), nullable=False),
        sa.Column("renewal_uplift", sa.Numeric(6, 2), nullable=False),
        sa.Column("commercial_risk", sa.Text(), nullable=False),
        sa.Column("approval_status", sa.Text(), nullable=False),
        sa.Column("pricing_notes", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "commercial_risk IN ('low', 'medium', 'high')",
            name="ck_pricing_notes_commercial_risk",
        ),
        sa.ForeignKeyConstraint(["opportunity_id"], ["opportunities.opportunity_id"]),
        sa.PrimaryKeyConstraint("pricing_note_id"),
    )


def downgrade() -> None:
    op.drop_table("pricing_notes")
    op.drop_table("contacts")
    op.drop_table("opportunities")
    op.drop_table("users")
    op.drop_table("accounts")
