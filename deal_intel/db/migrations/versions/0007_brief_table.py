"""brief table

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "briefs",
        sa.Column("run_id", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("markdown", sa.Text(), nullable=False),
        sa.Column("json", postgresql.JSONB(), nullable=False),
        sa.Column("max_access_level", sa.Text(), nullable=False),
        sa.Column("guardrail_results", postgresql.JSONB(), nullable=False),
        sa.Column("rendered_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "source IN ('run', 'replay', 'approval_update')", name="ck_briefs_source"
        ),
        sa.CheckConstraint(
            "max_access_level IN ('standard', 'restricted', 'sensitive_pricing')",
            name="ck_briefs_max_access_level",
        ),
        sa.ForeignKeyConstraint(["run_id"], ["runs.run_id"]),
        sa.PrimaryKeyConstraint("run_id", "version"),
    )


def downgrade() -> None:
    op.drop_table("briefs")
