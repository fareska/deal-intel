"""approval tables

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "approvals",
        sa.Column("approval_id", sa.Text(), nullable=False),
        sa.Column("run_id", sa.Text(), nullable=False),
        sa.Column("subject_id", sa.Text(), nullable=False),
        sa.Column("recommendation_ids", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("rule_ids", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("required_role", sa.Text(), nullable=False),
        sa.Column("account_id", sa.Text(), nullable=False),
        sa.Column("access_level", sa.Text(), nullable=False),
        sa.Column("eligible_user_ids", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("proposed_values", postgresql.JSONB(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("evidence_ids", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'rejected', 'expired', 'escalated')",
            name="ck_approvals_status",
        ),
        sa.CheckConstraint(
            "required_role IN ('deal_desk', 'sales_leader', 'legal', 'human_reviewer')",
            name="ck_approvals_required_role",
        ),
        sa.CheckConstraint(
            "access_level IN ('standard', 'restricted', 'sensitive_pricing')",
            name="ck_approvals_access_level",
        ),
        sa.ForeignKeyConstraint(["run_id"], ["runs.run_id"]),
        sa.PrimaryKeyConstraint("approval_id"),
        sa.UniqueConstraint(
            "run_id", "subject_id", "required_role", name="uq_approvals_subject_role"
        ),
    )
    op.create_index("ix_approvals_status_expires_at", "approvals", ["status", "expires_at"])
    op.create_index("ix_approvals_run_id", "approvals", ["run_id"])
    op.create_table(
        "approval_events",
        sa.Column("event_id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("approval_id", sa.Text(), nullable=False),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column("actor_user_id", sa.Text(), nullable=True),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "outcome IN ('approved', 'rejected', 'expired')", name="ck_approval_events_outcome"
        ),
        sa.ForeignKeyConstraint(["approval_id"], ["approvals.approval_id"]),
        sa.PrimaryKeyConstraint("event_id"),
    )
    op.create_index(
        "ix_approval_events_approval_id", "approval_events", ["approval_id", "event_id"]
    )
    # forbid_append_only_change() is created by revision 0005.
    op.execute(
        "CREATE TRIGGER approval_events_append_only BEFORE UPDATE OR DELETE ON approval_events "
        "FOR EACH ROW EXECUTE FUNCTION forbid_append_only_change()"
    )


def downgrade() -> None:
    op.drop_index("ix_approval_events_approval_id", table_name="approval_events")
    op.drop_table("approval_events")
    op.drop_index("ix_approvals_run_id", table_name="approvals")
    op.drop_index("ix_approvals_status_expires_at", table_name="approvals")
    op.drop_table("approvals")
